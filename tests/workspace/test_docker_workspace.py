"""Test DockerWorkspace import and basic functionality."""


def test_docker_workspace_import():
    """Test that DockerWorkspace can be imported from the new package."""
    from openhands.workspace import DockerWorkspace

    assert DockerWorkspace is not None
    assert hasattr(DockerWorkspace, "__init__")


def test_docker_workspace_inheritance():
    """Test that DockerWorkspace inherits from RemoteWorkspace."""
    from openhands.sdk.workspace import RemoteWorkspace
    from openhands.workspace import DockerWorkspace

    assert issubclass(DockerWorkspace, RemoteWorkspace)


def test_docker_workspace_controller_mode(monkeypatch):
    """Ensure DockerWorkspace can bootstrap through the sandbox controller."""
    from openhands.sandbox_controller.client import SandboxHandle
    from openhands.workspace.docker import workspace as docker_workspace_module

    created: dict[str, object] = {}

    class DummyClient:
        def __init__(self, base_url: str, *, timeout: float = 30.0):
            created["base_url"] = base_url
            created["timeout"] = timeout

        def create_sandbox(self, **kwargs):
            created.update(kwargs)
            return SandboxHandle(
                sandbox_id="sandbox-1",
                container_id="container-1",
                name="agent-server-test",
                ports={8000: 32123},
            )

        def delete_sandbox(self, sandbox_id: str) -> None:
            created["deleted"] = sandbox_id

        def close(self) -> None:
            created["closed"] = True

    def fail_execute_command(*args, **kwargs):
        raise AssertionError("execute_command should not be called")

    monkeypatch.setattr(
        docker_workspace_module,
        "SandboxControllerClient",
        DummyClient,
    )
    monkeypatch.setattr(
        docker_workspace_module,
        "execute_command",
        fail_execute_command,
    )
    monkeypatch.setattr(
        docker_workspace_module.DockerWorkspace,
        "_wait_for_health",
        lambda self, timeout=120.0: None,
    )

    workspace = docker_workspace_module.DockerWorkspace(
        server_image="ghcr.io/openhands/agent-server:latest",
        sandbox_controller_url="http://controller.local",
        controller_host_alias="localhost",
    )
    try:
        assert workspace.host == "http://localhost:32123"
        assert created["image"] == "ghcr.io/openhands/agent-server:latest"
        assert created["platform"] == "linux/amd64"
    finally:
        workspace.cleanup()

    assert created["deleted"] == "sandbox-1"
    assert created.get("closed") is True
