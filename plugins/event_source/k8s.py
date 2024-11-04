import asyncio
import logging
import json
import os
import urllib3
import re
from typing import Any, Dict
from six import string_types
from kubernetes_asyncio import config, dynamic, watch, client
from kubernetes_asyncio.client import ApiClient
from kubernetes_asyncio.client.exceptions import ApiException

# Initialize the logger
logger = logging.getLogger(__name__)

# Map string log levels to logging level constants
LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def set_log_level_from_string(logger, log_level_str):
    log_level = LOG_LEVELS.get(log_level_str.upper(), logging.INFO)
    logger.info(f"Setting log level to {log_level_str} ({log_level})")
    logger.setLevel(log_level)


try:
    import urllib3

    urllib3.disable_warnings()
except ImportError:
    # Handled in module setup
    pass


# Map kubernetes-client parameters to ansible parameters from kubernetes.core
AUTH_ARG_MAP = {
    "kubeconfig": "kubeconfig",
    "context": "context",
    "host": "host",
    "api_key": "api_key",
    "username": "username",
    "password": "password",
    "verify_ssl": "validate_certs",
    "ssl_ca_cert": "ca_cert",
    "cert_file": "client_cert",
    "key_file": "client_key",
    "proxy": "proxy",
    "no_proxy": "no_proxy",
    "proxy_headers": "proxy_headers",
    "persist_config": "persist_config",
}

AUTH_PROXY_HEADERS_SPEC = dict(
    proxy_basic_auth=dict(type="str", no_log=True),
    basic_auth=dict(type="str", no_log=True),
    user_agent=dict(type="str"),
)

AUTH_ARG_SPEC = {
    "kubeconfig": {"type": "raw"},
    "context": {},
    "host": {},
    "api_key": {"no_log": True},
    "username": {},
    "password": {"no_log": True},
    "validate_certs": {"type": "bool", "aliases": ["verify_ssl"]},
    "ca_cert": {"type": "path", "aliases": ["ssl_ca_cert"]},
    "client_cert": {"type": "path", "aliases": ["cert_file"]},
    "client_key": {"type": "path", "aliases": ["key_file"]},
    "proxy": {"type": "str"},
    "no_proxy": {"type": "str"},
    "proxy_headers": {"type": "dict", "options": AUTH_PROXY_HEADERS_SPEC},
    "persist_config": {"type": "bool"},
    "impersonate_user": {},
    "impersonate_groups": {"type": "list", "elements": "str"},
}


# Event types
INIT_DONE_EVENT = "INIT_DONE"


async def main(queue: asyncio.Queue, args: Dict[str, Any]):

    try:
        set_log_level_from_string(logger, args.get("log_level", "INFO"))

        logger.debug(f"Starting k8s eda source with args: {args}")

        api_version = args.get("api_version", "v1")
        kind = args.get("kind")

        if api_version is None or kind is None:
            raise Exception(f"'api_version' and 'kind' parameters must be provided")
        logger.info(f"Starting k8s eda source for {api_version}/{kind}")

        label_selectors = args.get("label_selectors", [])
        logger.info(f"label_selectors: {label_selectors}")
        field_selectors = args.get("field_selectors", [])
        logger.info(f"field_selectors: {field_selectors}")
        name = args.get("name")
        heartbeat_interval = args.get("heartbeat_interval", 60)
        logger.info(f"heartbeat_interval: {heartbeat_interval}")

        # Fix to avoid failing due to https://github.com/kubernetes-client/python/pull/2076
        if name:
            if not isinstance(field_selectors, list):
                field_selectors = field_selectors.split(",")
            field_selectors.append(f"metadata.name={name}")

        if isinstance(label_selectors, list):
            label_selectors = ",".join(label_selectors)

        if isinstance(field_selectors, list):
            field_selectors = ",".join(field_selectors)

        # Handle authentication
        auth_spec = _create_auth_spec(args)
        logger.info(f"auth_spec: {auth_spec}")
        configuration = await _create_configuration(auth_spec)

        # Connect to the API
        apiclient = ApiClient(configuration=configuration)
        dynamicclient = await dynamic.DynamicClient(apiclient)

        # Only add headers for proxy support if needed
        headers = _create_headers(args)
        for header, value in headers.items():
            _set_header(dynamicclient, header, value)

        # Configure the watcher object. It uses the default client configuration.
        watcher = watch.Watch()

        # Supply get/watch options
        options = dict(
            watcher=watcher,
            timeout=heartbeat_interval,
        )
        if label_selectors:
            options["label_selector"] = label_selectors
        if field_selectors:
            options["field_selector"] = field_selectors

        options.update(dict(("namespace", args[k]) for k in ["namespace"] if k in args))

        # Fetch existing objects and treat them as "ADDED" events
        api = await dynamicclient.resources.get(api_version=api_version, kind=kind)
        list_response = await api.get(**options)
        if list_response.status == "Failure":
            raise ApiException(list_response.message)
        # Update the resource version
        resource_version = int(list_response.metadata.resourceVersion)
        existing_objects = list_response.items
        if existing_objects:
            for obj in existing_objects:
                obj_as_dict = obj.to_dict()
                obj_as_json = json.dumps(obj_as_dict, indent=4)
                item = dict(type="ADDED", resource=obj_as_dict)
                object_name = _get_object_name(obj)
                object_path = (
                    options["namespace"] + "/" + object_name
                    if "namespace" in options
                    else object_name
                )
                logger.info("ADDED %s %s to queue", obj["kind"], object_path)
                logger.debug("Object Details: %s", obj_as_json)
                await queue.put(item)

        logging.debug(
            f"Will initiate watch with resource {kind}, version {resource_version}"
        )

        event_count = 0
        test_events_qty = args.get("test_events_qty", None)

        # Send an INIT_DONE_EVENT to indicate that we have processed all existing objects and the watch is initiating
        await queue.put(dict(type=INIT_DONE_EVENT))
        logger.info("INIT_DONE_EVENT queued")

        while True:
            try:
                # Get resourceVersion to determine where to start streaming events from
                options.update(dict(resource_version=resource_version))

                logging.debug(
                    f"Initiating watch of resource {kind} at version {resource_version}"
                )
                timed_out = True
                async for e in dynamicclient.watch(api, **options):
                    timed_out = False
                    raw_object = e["raw_object"]
                    object_name = _get_object_name(raw_object)
                    logger.info(
                        "%s %s %s to queue",
                        e["type"],
                        raw_object["kind"],
                        object_name,
                    )
                    logger.debug("Detected object: %s", raw_object)

                    await queue.put(dict(type=e["type"], resource=raw_object))

                    # Update resource_version to the latest event
                    resource_version = int(
                        e["raw_object"]["metadata"]["resourceVersion"]
                    )

                    if isinstance(test_events_qty, int) and test_events_qty > 0:
                        # Update the event count
                        event_count += 1

                # If we didn't receive any events, send a heartbeat event
                if timed_out:
                    event_count += 1

                # Include heartbeat events in the test event count
                if isinstance(test_events_qty, int) and event_count >= test_events_qty:
                    return
            except ApiException as e:
                if (label_selectors or field_selectors) and e.status == 410:
                    # Handle the 410 Gone error if the resource version was too old
                    match = re.search(
                        r"too old resource version: \d+ \((\d+)\)", e.reason
                    )
                    if match:
                        resource_version = int(match.group(1))
                    else:
                        logger.error(
                            "Failed to extract resource version from error reason"
                        )
                        raise
                    logger.debug(f"Watch failed: {e.reason}")
                    resource_version = int(
                        e.headers.get("ResourceVersion", resource_version)
                    )
                    continue
                else:
                    logger.error(f"API Exception caught: {e}", exc_info=True)
                    raise

    except Exception as e:
        logger.error(f"Exception caught in main: {e}", exc_info=True)
        raise

    finally:
        if watcher:
            logger.info("Stopping k8s eda source")
            watcher.stop()
        if apiclient:
            await apiclient.close()


# Get the object name from a dictionary
def _get_object_name(obj: Dict[str, Any]) -> str:
    return (
        obj["metadata"]["name"]
        if obj["kind"] == "Namespace"
        else obj["metadata"]["namespace"] + "/" + obj["metadata"]["name"]
    )


# Authentication functions from Kubernetes Core Module
# https://github.com/ansible-collections/kubernetes.core/blob/main/plugins/module_utils/k8s/client.py
def _create_auth_spec(args: Dict[str, Any]) -> Dict:
    auth: Dict = {}
    # If authorization variables aren't defined, look for them in environment variables
    for true_name, arg_name in AUTH_ARG_MAP.items():
        if arg_name in args and args.get(arg_name) is not None:
            auth[true_name] = args.get(arg_name)
        elif true_name in args and args.get(true_name) is not None:
            # Aliases in kwargs
            auth[true_name] = args.get(true_name)
        elif arg_name == "proxy_headers":
            # specific case for 'proxy_headers' which is a dictionary
            proxy_headers = {}
            for key in AUTH_PROXY_HEADERS_SPEC.keys():
                env_value = os.getenv(
                    "K8S_AUTH_PROXY_HEADERS_{0}".format(key.upper()), None
                )
                if env_value is not None:
                    if AUTH_PROXY_HEADERS_SPEC[key].get("type") == "bool":
                        env_value = env_value.lower() not in ["0", "false", "no"]
                    proxy_headers[key] = env_value
            if proxy_headers is not {}:
                auth[true_name] = proxy_headers
        else:
            env_value = os.getenv(
                "K8S_AUTH_{0}".format(arg_name.upper()), None
            ) or os.getenv("K8S_AUTH_{0}".format(true_name.upper()), None)
            if env_value is not None:
                if AUTH_ARG_SPEC[arg_name].get("type") == "bool":
                    env_value = env_value.lower() not in ["0", "false", "no"]
                auth[true_name] = env_value

    return auth


def _create_headers(args: Dict[str, Any]):
    header_map = {
        "impersonate_user": "Impersonate-User",
        "impersonate_groups": "Impersonate-Group",
    }

    headers = {}
    for arg_name, header_name in header_map.items():
        value = None
        if arg_name in args and args.get(arg_name) is not None:
            value = args.get(arg_name)
        else:
            value = os.getenv("K8S_AUTH_{0}".format(arg_name.upper()), None)
            if value is not None:
                if AUTH_ARG_SPEC[arg_name].get("type") == "list":
                    value = [x for x in value.split(",") if x != ""]
        if value:
            headers[header_name] = value
    return headers


def _set_header(client, header, value):
    if isinstance(value, list):
        for v in value:
            client.set_default_header(header_name=unique_string(header), header_value=v)
    else:
        client.set_default_header(header_name=header, header_value=value)


class unique_string(str):
    _low = None

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def lower(self):
        if self._low is None:
            lower = str.lower(self)
            if str.__eq__(lower, self):
                self._low = self
            else:
                self._low = unique_string(lower)
        return self._low


async def _create_configuration(auth: Dict):
    def auth_set(*names: list) -> bool:
        return all(auth.get(name) for name in names)

    if auth_set("host"):
        # Removing trailing slashes if any from hostname
        auth["host"] = auth.get("host").rstrip("/")
        logger.info(f"Using host: {auth['host']}")

    if (
        auth_set("username", "password", "host")
        or auth_set("api_key", "host")
        or auth_set("cert_file", "key_file", "host")
    ):
        # We have enough in the parameters to authenticate, no need to load incluster or kubeconfig
        logger.info("Using provided authentication parameters")
        pass
    elif auth_set("kubeconfig") or auth_set("context"):
        try:
            await _load_config(auth)
            logger.info("Using kubeconfig")
        except Exception as err:
            raise err

    else:
        # First try to do incluster config, then kubeconfig
        try:
            config.load_incluster_config()
            logger.info("Using incluster config")
        except config.ConfigException as e:
            logger.warning("In-cluster configuration failed: %s", e)
            try:
                await _load_config(auth)
            except Exception as err:
                raise err

    # Override any values in the default configuration with Ansible parameters
    configuration = client.Configuration().get_default_copy()
    for key, value in auth.items():
        if key in AUTH_ARG_MAP.keys() and value is not None:
            if key == "api_key":
                setattr(
                    configuration, key, {"authorization": "Bearer {0}".format(value)}
                )
            elif key == "proxy_headers":
                headers = urllib3.util.make_headers(**value)
                setattr(configuration, key, headers)
            else:
                setattr(configuration, key, value)

    # If the log level is debug, turn on debugging for the client
    if logger.isEnabledFor(logging.DEBUG):
        logger.info("Enable ApiClient debug logging")
        configuration.debug = True

    return configuration


async def _load_config(auth: Dict) -> None:
    kubeconfig = auth.get("kubeconfig")
    optional_arg = {
        "context": auth.get("context"),
        "persist_config": auth.get("persist_config"),
    }
    if kubeconfig:
        if isinstance(kubeconfig, string_types):
            await config.load_kube_config(config_file=kubeconfig, **optional_arg)
        elif isinstance(kubeconfig, dict):
            await config.load_kube_config_from_dict(
                config_dict=kubeconfig, **optional_arg
            )
    else:
        await config.load_kube_config(config_file=None, **optional_arg)


if __name__ == "__main__":

    class MockQueue:
        async def put(self, event):
            print(event)

    asyncio.run(main(MockQueue(), {}))
