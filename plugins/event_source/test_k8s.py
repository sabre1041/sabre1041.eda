import asyncio
import pytest
import time
from plugins.event_source.k8s import main, Watcher
from kubernetes import client, config

KUBECONFIG = ".pytest-kind/pytest-kind/kubeconfig"

INIT_DONE_TIMEOUT = 10
POD_CREATION_TIMEOUT = 60
NAMESPACE_CREATION_TIMEOUT = 10
HEARTBEAT_INTERVAL = 3


@pytest.fixture(scope="session")
def kind_cluster(kind_cluster):
    # This fixture will spin up a kind cluster for the duration of the session
    yield kind_cluster


@pytest.fixture(scope="session")
def k8s_client(kind_cluster):
    # Load kubeconfig and create a Kubernetes client
    config.load_kube_config(
        str(kind_cluster.kubeconfig_path)
    )  # Convert PosixPath to string
    return client.CoreV1Api()


@pytest.fixture(scope="function", autouse=True)
def setup_namespace(request, k8s_client):
    # Get the namespace from the command-line argument or use "pytest" as default
    namespace = request.config.getoption("--namespace")

    # Delete the namespace if it exists
    for ns in [namespace, "pytest-namespace"]:
        try:
            k8s_client.delete_namespace(name=ns)
            # Wait for the namespace to be deleted
            for _ in range(60):  # Retry for up to 60 seconds
                try:
                    k8s_client.read_namespace(name=ns)
                    time.sleep(1)
                except client.exceptions.ApiException as e:
                    if e.status == 404:
                        break  # Namespace is deleted
                    else:
                        raise
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise

    # Create the namespace
    namespace_manifest = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": namespace},
    }
    k8s_client.create_namespace(body=namespace_manifest)

    # Wait for the namespace to be created
    for _ in range(60):  # Retry for up to 60 seconds
        try:
            k8s_client.read_namespace(name=namespace)
            break  # Namespace is available
        except client.exceptions.ApiException:
            time.sleep(1)
    else:
        raise RuntimeError("Timeout waiting for namespace to be created")

    # Wait for the default service account to be created
    for _ in range(60):  # Retry for up to 60 seconds
        try:
            k8s_client.read_namespaced_service_account(
                name="default", namespace=namespace
            )
            break  # Service account is available
        except client.exceptions.ApiException:
            time.sleep(1)
    else:
        raise RuntimeError("Timeout waiting for service account to be created")

    return namespace


async def wait_for_event(
    queue, event_type=Watcher.INIT_DONE_EVENT, timeout=INIT_DONE_TIMEOUT
):
    """
    Wait for a specific event type to appear in the queue within a given timeout.

    Args:
        queue (asyncio.Queue): The queue to monitor for events.
        event_type (str): The type of event to wait for.
        timeout (int): The maximum time to wait for the event, in seconds.

    Returns:
        list: A list of events received before the specified event type.
        bool: False if the timeout is reached before the specified event type is received.

    Raises:
        asyncio.TimeoutError: If the timeout is reached before any event is received.
    """
    start_time = time.time()
    events = []
    try:
        # Wait for the INIT_DONE event with a timeout
        while True:
            event = await asyncio.wait_for(queue.get(), timeout)
            events.append(event)
            if event["type"] == event_type:
                return events
            if time.time() - start_time > timeout:
                return events
    except asyncio.TimeoutError:
        raise


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "test_case",
    [
        {
            "args": {
                "api_version": "v1",
                "kind": "Namespace",
                "kubeconfig": KUBECONFIG,
                "test_events_qty": 1,
                "heartbeat_interval": HEARTBEAT_INTERVAL,
            },
            "k8sclient_objects": [
                {
                    "create": "create_namespace",
                    "body": {
                        "apiVersion": "v1",
                        "kind": "Namespace",
                        "metadata": {"name": "pytest-namespace"},
                    },
                },
            ],
        },
        {
            "args": {
                "api_version": "v1",
                "kinds": [
                    {
                        "kind": "Namespace",
                    },
                    {
                        "kind": "ConfigMap",
                    },
                ],
                "kubeconfig": KUBECONFIG,
                "test_events_qty": 1,
                "heartbeat_interval": HEARTBEAT_INTERVAL,
            },
            "k8sclient_objects": [
                {
                    "create": "create_namespace",
                    "body": {
                        "apiVersion": "v1",
                        "kind": "Namespace",
                        "metadata": {"name": "pytest-namespace"},
                    },
                },
                {
                    "create": "create_namespaced_config_map",
                    "body": {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {
                            "name": "example-configmap",
                            "namespace": "pytest-namespace",
                        },
                        "data": {
                            "key": "value",
                        },
                    },
                },
            ],
        },
    ],
    ids=["namespace_kind", "namespace_configmap_kinds"],
)
async def test_create(k8s_client, test_case):
    # Use a real asyncio.Queue
    queue = asyncio.Queue()

    # Run the main function in the background
    main_task = asyncio.create_task(main(queue, test_case["args"]))

    kinds = []
    if "kind" in test_case["args"]:
        kinds.append(test_case["args"]["kind"])
    if "kinds" in test_case["args"]:
        kinds.extend(test_case["args"]["kinds"])

    for _ in range(0, len(kinds)):
        # Wait for each watch to finish initializing
        events = await wait_for_event(
            queue, event_type=Watcher.INIT_DONE_EVENT, timeout=INIT_DONE_TIMEOUT
        )
        assert events
        assert len(events) == 1
        assert events[-1]["type"] == Watcher.INIT_DONE_EVENT

    k8sclient_objects = test_case["k8sclient_objects"]

    # Helper to invoke all object methods by name
    def call_methods_by_name(method_name):
        # Map kind to body
        kinds = {}
        for object in k8sclient_objects:
            method = object.get(method_name)
            if method:
                body = object["body"]
                metadata = body["metadata"]
                namespace = metadata.get("namespace")
                kind = body["kind"]
                # Only expect one of kind
                assert kind not in kinds
                kinds[kind] = body
                k8s_client_method = getattr(k8s_client, method)
                if namespace:
                    k8s_client_method(namespace=namespace, body=body)
                else:
                    k8s_client_method(body=body)
        return kinds

    # Create all client objects
    created_kinds = call_methods_by_name("create")

    # Wait for the main function to complete
    await asyncio.wait_for(main_task, timeout=NAMESPACE_CREATION_TIMEOUT)

    # Make sure there is only one item in the queue
    queue_len = queue.qsize()
    assert queue_len >= len(created_kinds)

    # Get all the kinds that were created
    added_kinds = {}

    # Retrieve all items from the queue to get the last item
    try:
        while True:
            event = queue.get_nowait()
            event_type = event["type"]
            if event_type == "ADDED":
                event_kind = event["resource"]["kind"]
                resource = event["resource"]
                added_kinds[event_kind] = resource
                assert (
                    resource["metadata"]["name"]
                    == added_kinds[event_kind]["metadata"]["name"]
                )

    except asyncio.QueueEmpty:
        pass

    # Ensure we received the correct kinds
    assert added_kinds.keys() == created_kinds.keys()


@pytest.mark.asyncio
async def test_pod(k8s_client, request):
    namespace = request.config.getoption("--namespace")

    # Mock the arguments
    args = {
        "api_version": "v1",
        "kind": "Pod",
        "label_selectors": [],
        "field_selectors": [],
        "name": "example-pod",
        "namespace": namespace,
        "kubeconfig": KUBECONFIG,
        "test_events_qty": 1,
        "heartbeat_interval": 10,
    }

    # Use a real asyncio.Queue
    queue = asyncio.Queue()

    # Run the main function in the background
    main_task = asyncio.create_task(main(queue, args))

    # Wait for the main function to be ready
    events = await wait_for_event(
        queue, event_type=Watcher.INIT_DONE_EVENT, timeout=INIT_DONE_TIMEOUT
    )
    assert events
    assert len(events) > 0
    assert events[-1]["type"] == Watcher.INIT_DONE_EVENT

    # Create a pod in the kind cluster
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "example-pod"},
        "spec": {
            "containers": [
                {
                    "name": "example-container",
                    "image": "busybox",
                    "command": ["sleep", "1"],
                }
            ],
            "terminationGracePeriodSeconds": 0,  # Set a short termination grace period
        },
    }
    k8s_client.create_namespaced_pod(namespace=namespace, body=pod_manifest)

    # Wait for the main function to complete
    await asyncio.wait_for(main_task, timeout=POD_CREATION_TIMEOUT)

    # Assertions
    assert not queue.empty()  # Ensure the queue is not empty
    item = await queue.get()
    assert item["type"] == "ADDED"  # Check the type of the item
    assert (
        item["resource"]["metadata"]["name"] == "example-pod"
    )  # Check the resource name
