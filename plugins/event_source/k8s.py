import asyncio
import logging
import json
import os
import urllib3
import re
import signal
from typing import Any, Dict
from six import string_types
from kubernetes_asyncio import config, dynamic, watch, client
from kubernetes_asyncio.client import ApiClient
from kubernetes_asyncio.client.exceptions import ApiException


class Watcher:
    # Event types
    INIT_DONE_EVENT = "INIT_DONE"

    # Default heartbeat interval
    DEFAULT_HEARTBEAT_INTERVAL = 60

    def __init__(
        self,
        queue: asyncio.Queue,
        args: Dict[str, Any],
        configuration: client.Configuration,
        headers: Dict[str, Any],
    ):
        self.queue = queue
        self.args = args
        self.configuration = configuration
        self.headers = headers
        self.api_version = args.get("api_version", "v1")
        self.kind = args.get("kind")
        self.label_selectors = args.get("label_selectors", [])
        self.field_selectors = args.get("field_selectors", [])
        self.name = args.get("name")
        self.heartbeat_interval = args.get(
            "heartbeat_interval", Watcher.DEFAULT_HEARTBEAT_INTERVAL
        )
        self.test_events_qty = args.get("test_events_qty", None)
        self.event_count = 0
        self.resource_version = None
        self.logger = logging.getLogger(self.kind)
        self.logger.setLevel(args.get("log_level", logging.INFO))
        self.watcher = None
        self.apiclient = None
        self.dynamicclient = None

    async def run(self):
        try:
            self.logger.debug(f"Starting k8s eda source with args: {self.args}")

            if self.api_version is None or self.kind is None:
                raise Exception(f"'api_version' and 'kind' parameters must be provided")
            self.logger.info(
                f"Starting k8s eda source for {self.api_version}/{self.kind}"
            )

            self.logger.info(f"label_selectors: {self.label_selectors}")
            self.logger.info(f"field_selectors: {self.field_selectors}")
            self.logger.info(f"heartbeat_interval: {self.heartbeat_interval}")

            # Fix to avoid failing due to https://github.com/kubernetes-client/python/pull/2076
            if self.name:
                if not isinstance(self.field_selectors, list):
                    self.field_selectors = self.field_selectors.split(",")
                self.field_selectors.append(f"metadata.name={self.name}")

            if isinstance(self.label_selectors, list):
                self.label_selectors = ",".join(self.label_selectors)

            if isinstance(self.field_selectors, list):
                self.field_selectors = ",".join(self.field_selectors)

            # Connect to the API
            self.apiclient = ApiClient(configuration=self.configuration)
            self.dynamicclient = await dynamic.DynamicClient(self.apiclient)

            # Only add headers for proxy support if needed
            for header, value in self.headers.items():
                self._set_header(self.dynamicclient, header, value)

            # Configure the watcher object. It uses the default client configuration.
            self.watcher = watch.Watch()

            # Supply get/watch options
            options = dict(
                watcher=self.watcher,
                timeout=self.heartbeat_interval,
            )
            if self.label_selectors:
                options["label_selector"] = self.label_selectors
            if self.field_selectors:
                options["field_selector"] = self.field_selectors

            options.update(
                dict(
                    ("namespace", self.args[k]) for k in ["namespace"] if k in self.args
                )
            )

            # Fetch existing objects and treat them as "ADDED" events
            api = await self.dynamicclient.resources.get(
                api_version=self.api_version, kind=self.kind
            )
            list_response = await api.get(**options)
            if list_response.status == "Failure":
                raise ApiException(list_response.message)
            # Update the resource version
            self.resource_version = int(list_response.metadata.resourceVersion)
            existing_objects = list_response.items
            if existing_objects:
                for obj in existing_objects:
                    obj_as_dict = obj.to_dict()
                    obj_as_json = json.dumps(obj_as_dict, indent=4)
                    item = dict(type="ADDED", resource=obj_as_dict)
                    object_name = self._get_object_name(obj)
                    object_path = (
                        options["namespace"] + "/" + object_name
                        if "namespace" in options
                        else object_name
                    )
                    self.logger.info("ADDED %s %s to queue", obj["kind"], object_path)
                    self.logger.debug("Object Details: %s", obj_as_json)
                    await self.queue.put(item)

            self.logger.debug(
                f"Will initiate watch with resource {self.kind}, version {self.resource_version}"
            )

            # Send an INIT_DONE_EVENT to indicate that we have processed all existing objects and the watch is initiating
            await self.queue.put(dict(type=Watcher.INIT_DONE_EVENT))
            self.logger.info("INIT_DONE_EVENT queued")

            while True:
                try:
                    # Get resourceVersion to determine where to start streaming events from
                    options.update(dict(resource_version=self.resource_version))

                    self.logger.debug(
                        f"Initiating watch of resource {self.kind} at version {self.resource_version}"
                    )
                    timed_out = True
                    async for e in self.dynamicclient.watch(api, **options):
                        timed_out = False
                        raw_object = e["raw_object"]
                        object_name = self._get_object_name(raw_object)
                        self.logger.info(
                            "%s %s %s to queue",
                            e["type"],
                            raw_object["kind"],
                            object_name,
                        )
                        self.logger.debug("Detected object: %s", raw_object)

                        await self.queue.put(dict(type=e["type"], resource=raw_object))

                        # Update resource_version to the latest event
                        self.resource_version = int(
                            e["raw_object"]["metadata"]["resourceVersion"]
                        )

                        if (
                            isinstance(self.test_events_qty, int)
                            and self.test_events_qty > 0
                        ):
                            # Update the event count
                            self.event_count += 1

                    # If we didn't receive any events, send a heartbeat event
                    if timed_out:
                        self.event_count += 1

                    # Include heartbeat events in the test event count
                    if (
                        isinstance(self.test_events_qty, int)
                        and self.event_count >= self.test_events_qty
                    ):
                        return
                except ApiException as e:
                    if (
                        self.label_selectors or self.field_selectors
                    ) and e.status == 410:
                        # Handle the 410 Gone error if the resource version was too old
                        match = re.search(
                            r"too old resource version: \d+ \((\d+)\)", e.reason
                        )
                        if match:
                            self.resource_version = int(match.group(1))
                        else:
                            self.logger.error(
                                "Failed to extract resource version from error reason"
                            )
                            raise
                        self.logger.debug(f"Watch failed: {e.reason}")
                        continue
                    else:
                        self.logger.error(f"API Exception caught: {e}", exc_info=True)
                        raise

        except Exception as e:
            self.logger.error(f"Exception caught in main: {e}", exc_info=True)
            raise

        finally:
            await self.stop()

    async def stop(self):
        if self.watcher:
            self.logger.info("Stopping k8s eda source")
            self.watcher.stop()
        if self.apiclient:
            await self.apiclient.close()

    def _get_object_name(self, obj: Dict[str, Any]) -> str:
        return (
            obj["metadata"]["name"]
            if obj["kind"] == "Namespace"
            else obj["metadata"]["namespace"] + "/" + obj["metadata"]["name"]
        )

    def _set_header(self, client, header, value):
        if isinstance(value, list):
            for v in value:
                client.set_default_header(
                    header_name=Watcher.unique_string(header), header_value=v
                )
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
                    self._low = Watcher.unique_string(lower)
            return self._low


class WatchController:
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

    # Used for proxy support
    AUTH_PROXY_HEADERS_SPEC = dict(
        proxy_basic_auth=dict(type="str", no_log=True),
        basic_auth=dict(type="str", no_log=True),
        user_agent=dict(type="str"),
    )

    # Authentication arguments
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

    # Map string log levels to logging level constants
    LOG_LEVELS = {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
        "NOTSET": logging.NOTSET,
    }

    WATCHER_ARGS = {
        "api_version",
        "kind",
        "name",
        "namespace",
        "label_selectors",
        "field_selectors",
        "log_level",
    }

    def __init__(self, queue: asyncio.Queue, args: Dict[str, Any]):
        self.queue = queue
        self.args = args
        self.watchers = []
        self.logger = logging.getLogger(__name__)

        # Allow log levels to be set by kind
        if "log_level" in args:
            log_level = WatchController._log_level_from_string(
                args.get("log_level", "INFO")
            )
            args["log_level"] = log_level
            self.logger.setLevel(log_level)

        if "kinds" in args:
            if not isinstance(args["kinds"], list):
                raise ValueError("kinds must be a list")

            for kind in args["kinds"]:
                # If kind is not a dict, throw an error
                if not isinstance(kind, dict):
                    raise ValueError(
                        f"kind must be a dictionary, valid keys are {self.WATCHER_ARGS}."
                    )

                # Make sure all the keys that are provided are allowed
                # Use set difference to find offending keys
                offending_keys = set(kind.keys()).difference(self.WATCHER_ARGS)
                if offending_keys:
                    raise ValueError(
                        f"Invalid keys {offending_keys} in kind. Valid keys are {self.WATCHER_ARGS}."
                    )

                # Find the keys that are in args but not in kind that are not in AUTH_ARG_MAP.
                # Copy them into the kind.
                for key in args.keys():
                    if (
                        key not in kind
                        and key not in self.AUTH_ARG_MAP
                        and key != "kinds"
                    ):
                        kind[key] = args[key]

                # If log_level is set in the kind, set the logger level
                if "log_level" in kind:
                    kind["log_level"] = WatchController._log_level_from_string(
                        kind["log_level"]
                    )

    async def run(self):
        try:
            # Handle authentication
            auth_spec = self._create_auth_spec(self.args)
            self.logger.info(f"auth_spec: {auth_spec}")
            configuration = await self._create_configuration(auth_spec)

            # Configure headers
            headers = self._create_headers(self.args)

            # Run the watcher(s)
            if "kind" in self.args:
                self.watchers.append(
                    Watcher(self.queue, self.args, configuration, headers)
                )
            if "kinds" in self.args:
                for kind in self.args["kinds"]:
                    self.watchers.append(
                        Watcher(self.queue, kind, configuration, headers)
                    )

            # Await all watchers concurrently
            await asyncio.gather(*(watcher.run() for watcher in self.watchers))

        except Exception as e:
            self.logger.error(f"Exception caught in main: {e}", exc_info=True)
            raise

    async def stop(self):
        # Stop all watchers
        for watcher in self.watchers:
            await watcher.stop()

    def _log_level_from_string(log_level_str):
        log_level = WatchController.LOG_LEVELS.get(log_level_str.upper(), logging.INFO)
        if log_level is None:
            raise ValueError(f"Invalid log level: {log_level_str}")
        return log_level

    async def _create_configuration(self, auth: Dict):
        def auth_set(*names: list) -> bool:
            return all(auth.get(name) for name in names)

        if auth_set("host"):
            # Removing trailing slashes if any from hostname
            auth["host"] = auth.get("host").rstrip("/")
            self.logger.info(f"Using host: {auth['host']}")

        if (
            auth_set("username", "password", "host")
            or auth_set("api_key", "host")
            or auth_set("cert_file", "key_file", "host")
        ):
            # We have enough in the parameters to authenticate, no need to load incluster or kubeconfig
            self.logger.info("Using provided authentication parameters")
            pass
        elif auth_set("kubeconfig") or auth_set("context"):
            try:
                await self._load_config(auth)
                self.logger.info("Using kubeconfig")
            except Exception as err:
                raise err

        else:
            # First try to do incluster config, then kubeconfig
            try:
                config.load_incluster_config()
                self.logger.info("Using incluster config")
            except config.ConfigException as e:
                self.logger.warning("In-cluster configuration failed: %s", e)
                try:
                    await self._load_config(auth)
                except Exception as err:
                    raise err

        # Override any values in the default configuration with Ansible parameters
        configuration = client.Configuration().get_default_copy()
        for key, value in auth.items():
            if key in self.AUTH_ARG_MAP.keys() and value is not None:
                if key == "api_key":
                    setattr(
                        configuration,
                        key,
                        {"authorization": "Bearer {0}".format(value)},
                    )
                elif key == "proxy_headers":
                    headers = urllib3.util.make_headers(**value)
                    setattr(configuration, key, headers)
                else:
                    setattr(configuration, key, value)

        # If the log level is debug, turn on debugging for the client
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.info("Enable ApiClient debug logging")
            configuration.debug = True

        return configuration

    async def _load_config(self, auth: Dict) -> None:
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

    def _create_auth_spec(self, args: Dict[str, Any]) -> Dict:
        auth: Dict = {}
        # If authorization variables aren't defined, look for them in environment variables
        for true_name, arg_name in self.AUTH_ARG_MAP.items():
            if arg_name in args and args.get(arg_name) is not None:
                auth[true_name] = args.get(arg_name)
            elif true_name in args and args.get(true_name) is not None:
                # Aliases in kwargs
                auth[true_name] = args.get(true_name)
            elif arg_name == "proxy_headers":
                # specific case for 'proxy_headers' which is a dictionary
                proxy_headers = {}
                for key in self.AUTH_PROXY_HEADERS_SPEC.keys():
                    env_value = os.getenv(
                        "K8S_AUTH_PROXY_HEADERS_{0}".format(key.upper()), None
                    )
                    if env_value is not None:
                        if self.AUTH_PROXY_HEADERS_SPEC[key].get("type") == "bool":
                            env_value = env_value.lower() not in ["0", "false", "no"]
                        proxy_headers[key] = env_value
                if proxy_headers is not {}:
                    auth[true_name] = proxy_headers
            else:
                env_value = os.getenv(
                    "K8S_AUTH_{0}".format(arg_name.upper()), None
                ) or os.getenv("K8S_AUTH_{0}".format(true_name.upper()), None)
                if env_value is not None:
                    if self.AUTH_ARG_SPEC[arg_name].get("type") == "bool":
                        env_value = env_value.lower() not in ["0", "false", "no"]
                    auth[true_name] = env_value

        return auth

    async def _create_configuration(self, auth: Dict):
        def auth_set(*names: list) -> bool:
            return all(auth.get(name) for name in names)

        if auth_set("host"):
            # Removing trailing slashes if any from hostname
            auth["host"] = auth.get("host").rstrip("/")
            self.logger.info(f"Using host: {auth['host']}")

        if (
            auth_set("username", "password", "host")
            or auth_set("api_key", "host")
            or auth_set("cert_file", "key_file", "host")
        ):
            # We have enough in the parameters to authenticate, no need to load incluster or kubeconfig
            self.logger.info("Using provided authentication parameters")
            pass
        elif auth_set("kubeconfig") or auth_set("context"):
            try:
                await self._load_config(auth)
                self.logger.info("Using kubeconfig")
            except Exception as err:
                raise err

        else:
            # First try to do incluster config, then kubeconfig
            try:
                config.load_incluster_config()
                self.logger.info("Using incluster config")
            except config.ConfigException as e:
                self.logger.warning("In-cluster configuration failed: %s", e)
                try:
                    await self._load_config(auth)
                except Exception as err:
                    raise err

        # Override any values in the default configuration with Ansible parameters
        configuration = client.Configuration().get_default_copy()
        for key, value in auth.items():
            if key in self.AUTH_ARG_MAP.keys() and value is not None:
                if key == "api_key":
                    setattr(
                        configuration,
                        key,
                        {"authorization": "Bearer {0}".format(value)},
                    )
                elif key == "proxy_headers":
                    headers = urllib3.util.make_headers(**value)
                    setattr(configuration, key, headers)
                else:
                    setattr(configuration, key, value)

        # If the log level is debug, turn on debugging for the client
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.info("Enable ApiClient debug logging")
            configuration.debug = True

        return configuration

    def _create_headers(self, args: Dict[str, Any]):
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
                    if self.AUTH_ARG_SPEC[arg_name].get("type") == "list":
                        value = [x for x in value.split(",") if x != ""]
            if value:
                headers[header_name] = value
        return headers


async def main(queue: asyncio.Queue, args: Dict[str, Any]):
    watch_controller = WatchController(queue, args)

    def handle_signal():
        asyncio.create_task(watch_controller.stop())

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    await watch_controller.run()


if __name__ == "__main__":

    class MockQueue:
        async def put(self, event):
            print(event)

    queue = MockQueue()
    args = {
        "api_version": "v1",
        "kind": "Pod",
        "log_level": "DEBUG",
        "label_selectors": ["app=myapp"],
        "field_selectors": ["metadata.name=myapp"],
        "name": "myapp",
        "heartbeat_interval": 60,
        "test_events_qty": 10,
    }
    asyncio.run(main(queue, args))
