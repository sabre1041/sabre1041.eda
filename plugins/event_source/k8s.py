import asyncio
import logging
from ansible.module_utils.six import iteritems, string_types
from kubernetes import config, dynamic, watch, client
from kubernetes.client import api_client
from typing import Any, Dict
import os

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


async def main(queue: asyncio.Queue, args: Dict[str, Any]):
    logger = logging.getLogger()
    logger.info("Running k8s eda source")

    try:

        api_version = args.get("api_version", "v1")
        kind = args.get("kind")

        if api_version is None or args.get("kind") is None:
            raise Exception(f"'api_version' and 'kind' parameters must be provided")

        watcher = watch.Watch()

        label_selector = args.get("label_selectors", [])
        field_selector = args.get("field_selectors", [])
        name = args.get("name")

        # Fix to avoid failing due to https://github.com/kubernetes-client/python/pull/2076
        ####
        if name:
            if not isinstance(field_selector, list):
                field_selector = field_selector.split(',')
            field_selector.append(f"metadata.name={name}")
        ####

        if isinstance(label_selector, list):
            label_selector = ",".join(label_selector)

        if isinstance(field_selector, list):
            field_selector = ",".join(field_selector)

        options = dict(
            watcher=watcher,
            label_selector=label_selector,
            field_selector=field_selector,
        )

        options.update(dict((k, args[k]) for k in ['namespace'] if k in args))

        # Handle authentication
        auth_spec = _create_auth_spec(args)
        configuration = _create_configuration(auth_spec)
        headers = _create_headers(args)
        client = dynamic.DynamicClient(
                api_client.ApiClient(configuration=configuration,)
                )

        for header, value in headers.items():
            _set_header(client, header, value)

        while True:
            try:
                api = client.resources.get(api_version=api_version, kind=kind)

                # Get resourceVersion to determine where to start streaming events from
                options.update(dict(resource_version=int(client.get(api, **options)["metadata"]["resourceVersion"])))

                for e in client.watch(api, **options, ):
                    await queue.put(dict(type=e["type"], resource=e["raw_object"]))
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error("Exception caught: %s", e)
    finally:
        logger.info("Stopping k8s eda source")
        watcher.stop()

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



def _create_configuration(auth: Dict):
    def auth_set(*names: list) -> bool:
        return all(auth.get(name) for name in names)

    if auth_set("host"):
        # Removing trailing slashes if any from hostname
        auth["host"] = auth.get("host").rstrip("/")

    if (
        auth_set("username", "password", "host")
        or auth_set("api_key", "host")
        or auth_set("cert_file", "key_file", "host")
    ):
        # We have enough in the parameters to authenticate, no need to load incluster or kubeconfig
        pass
    elif auth_set("kubeconfig") or auth_set("context"):
        try:
            _load_config(auth)
        except Exception as err:
            raise err

    else:
        # First try to do incluster config, then kubeconfig
        try:
            config.load_incluster_config()
        except config.ConfigException:
            try:
                _load_config(auth)
            except Exception as err:
                raise err

    # Override any values in the default configuration with Ansible parameters
    # As of kubernetes-client v12.0.0, get_default_copy() is required here
    try:
        configuration = client.Configuration().get_default_copy()
    except AttributeError:
        configuration = client.Configuration()

    for key, value in iteritems(auth):
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

    return configuration



def _load_config(auth: Dict) -> None:
    kubeconfig = auth.get("kubeconfig")
    optional_arg = {
        "context": auth.get("context"),
        "persist_config": auth.get("persist_config"),
    }
    if kubeconfig:
        if isinstance(kubeconfig, string_types):
            config.load_kube_config(config_file=kubeconfig, **optional_arg)
        elif isinstance(kubeconfig, dict):
            config.load_kube_config_from_dict(
                config_dict=kubeconfig, **optional_arg
            )
    else:
        config.load_kube_config(config_file=None, **optional_arg)


if __name__ == "__main__":

    class MockQueue:
        async def put(self, event):
            print(event)

    asyncio.run(main(MockQueue(), {}))
