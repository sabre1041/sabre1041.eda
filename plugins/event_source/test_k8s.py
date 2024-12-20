import asyncio
import pytest
import time
from kubernetes import client, config

from plugins.event_source.k8s import (
    main,
    Watcher,
)


# Path to the kubeconfig file for the kind cluster
KUBECONFIG = ".pytest-kind/pytest-kind/kubeconfig"

# Timeout constants
INIT_DONE_TIMEOUT = 10
POD_CREATION_TIMEOUT = 60
NAMESPACE_CREATION_TIMEOUT = 10
HEARTBEAT_INTERVAL = 3


@pytest.fixture(scope="session")
def kind_cluster(kind_cluster):
    """
    Fixture to spin up a kind cluster for the duration of the session.
    """
    yield kind_cluster


@pytest.fixture(scope="session")
def k8s_client(kind_cluster):
    """
    Fixture to load kubeconfig and create a Kubernetes client.

    :param kind_cluster: The kind cluster fixture.
    :return: Kubernetes CoreV1Api client.
    """
    config.load_kube_config(
        str(kind_cluster.kubeconfig_path)
    )  # Convert PosixPath to string
    return client.CoreV1Api()


@pytest.fixture(scope="function", autouse=True)
def setup_namespace(request, k8s_client):
    """
    Fixture to set up the namespace for each test function.

    :param request: The pytest request object.
    :param k8s_client: The Kubernetes client fixture.
    """
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
                "kinds": [
                    {
                        "api_version": "v1",
                        "kind": "Namespace",
                    },
                    {
                        "api_version": "v1",
                        "kind": "ConfigMap",
                        "namespace": "pytest-namespace",
                    },
                ],
                "kubeconfig": KUBECONFIG,
                "test_events_qty": 3,
                "heartbeat_interval": HEARTBEAT_INTERVAL,
            },
            # An extra ConfigMap is created for the new Namespace
            "created_watch_count": 3,
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
        {
            "args": {
                "kind": "ConfigMap",
                "changed_fields": ["data", "metadata.annotations.foo"],
                "kubeconfig": KUBECONFIG,
                "test_events_qty": 5,
                "heartbeat_interval": HEARTBEAT_INTERVAL,
            },
            "created_watch_count": 2,
            "modified_watch_count": 3,
            "k8sclient_objects": [
                {
                    "create": "create_namespaced_config_map",
                    "body": {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {
                            "name": "change-me",
                            "namespace": "pytest",
                            "description": "CONFIG MAP TO MODIFY",
                            "annotations": {
                                "foo": "bar",
                            },
                        },
                        "data": {
                            "key": "old",
                        },
                    },
                },
                {
                    "create": "create_namespaced_config_map",
                    "body": {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {
                            "name": "static",
                            "namespace": "pytest",
                            "description": "STATIC CONFIG MAP",
                        },
                        "data": {
                            "key": "no change ever",
                        },
                    },
                },
                {
                    "modify": "patch_namespaced_config_map",
                    "body": {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {
                            "name": "change-me",
                            "namespace": "pytest",
                            "description": "CHANGE 1",
                        },
                        "data": {
                            "key": "new",
                        },
                    },
                },
                {
                    "modify": "patch_namespaced_config_map",
                    "body": {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {
                            "name": "change-me",
                            "namespace": "pytest",
                            "description": "CHANGE 2",
                        },
                        "data": {
                            "key": "new2",
                        },
                    },
                },
                {
                    "modify": "patch_namespaced_config_map",
                    "body": {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {
                            "name": "static",
                            "namespace": "pytest",
                            # Description should trigger an event
                            "description": "ONLY UPDATING DESCRIPTION",
                        },
                    },
                },
                {
                    "modify": "patch_namespaced_config_map",
                    "body": {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {
                            "name": "static",
                            "namespace": "pytest",
                            # Annotations change should not trigger event
                            "annotations": {
                                "foo": "CHANGED",
                            },
                        },
                    },
                },
            ],
        },
    ],
    ids=[
        "create_namespace_kind",
        "create_namespace_configmap_kinds",
        "modify_configmap_changed_fields",
    ],
)
async def test_batch(k8s_client, test_case):
    """
    Test case to verify Kubernetes events are received correctly.

    :param k8s_client: The Kubernetes client fixture.
    :param test_case: The test case parameters.
    """
    # Use a real asyncio.Queue
    queue = asyncio.Queue()

    # Run the main function in the background
    args = test_case["args"]
    main_task = asyncio.create_task(main(queue, args))

    kinds = []
    if "kind" in args:
        kinds.append(args["kind"])
    if "kinds" in args:
        kinds.extend(args["kinds"])

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
        bodies = []
        for object in k8sclient_objects:
            method = object.get(method_name)
            if method:
                body = object["body"]
                metadata = body["metadata"]
                namespace = metadata.get("namespace")
                name = metadata.get("name")
                bodies.append(body)
                k8s_client_method = getattr(k8s_client, method)
                kwargs = dict(body=body)
                if namespace:
                    kwargs.update(namespace=namespace)
                if method_name != "create":
                    kwargs.update(name=name)
                k8s_client_method(**kwargs)
        return bodies

    # Create all client objects
    created = call_methods_by_name("create")

    # Modify all client objects
    modified = call_methods_by_name("modify")

    # Wait for the main function to complete
    await asyncio.wait_for(main_task, timeout=NAMESPACE_CREATION_TIMEOUT)

    # Make sure there is only one item in the queue
    queue_len = queue.qsize()
    assert queue_len == args["test_events_qty"]

    # Ensure the correct number of kinds were created/added
    # Retrieve all items from the queue to get the last item
    added_count = 0
    modified_count = 0
    try:
        while True:
            event = queue.get_nowait()
            event_type = event["type"]
            assert event_type in ["ADDED", "MODIFIED"]
            if event_type == "ADDED":
                added_count += 1
            elif event_type == "MODIFIED":
                modified_count += 1

    except asyncio.QueueEmpty:
        pass

    assert added_count == test_case.get("created_watch_count", len(created))
    assert modified_count == test_case.get("modified_watch_count", len(modified))


@pytest.mark.asyncio
async def test_pod(k8s_client, request):
    """
    Test case to verify Pod events are received correctly.

    :param k8s_client: The Kubernetes client fixture.
    :param request: The pytest request object.
    """
    namespace = request.config.getoption("--namespace")

    # Mock the arguments
    args = {
        "api_version": "v1",
        "kind": "Pod",
        "label_selectors": ["type=eda"],
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
        "metadata": {
            "name": "example-pod",
            "labels": {
                "type": "eda",
            },
        },
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
