def pytest_addoption(parser):
    parser.addoption(
        "--namespace",
        action="store",
        default="pytest",
        help="Kubernetes namespace to use for tests"
    )