from __future__ import annotations

import subprocess
from typing import Any

from fastapi.testclient import TestClient

from openhands.sandbox_controller.app import SandboxSettings, create_app


class DummyCompletedProcess(subprocess.CompletedProcess[str]):
    def __init__(self, args: list[str], stdout: str = "", stderr: str = "", returncode: int = 0):
        super().__init__(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def build_test_app(monkeypatch, tmp_path) -> TestClient:
    from openhands.sandbox_controller import app as app_module

    settings = SandboxSettings(
        allowed_image_prefixes=("ghcr.io/openhands/",),
        allowed_mount_targets=("/workspace",),
        port_min=41000,
        port_max=41010,
        name_prefix="test-sandbox-",
        max_command_args=16,
    )

    monkeypatch.setattr(app_module, "_check_port_available", lambda port: True)

    async def fake_stop_container(container_id: str) -> None:  # pragma: no cover - simple stub
        return None

    monkeypatch.setattr(app_module, "_stop_container", fake_stop_container)

    run_history: list[list[str]] = []

    async def fake_run_subprocess(command: list[str]) -> DummyCompletedProcess:
        run_history.append(command)
        return DummyCompletedProcess(command, stdout="container123\n")

    monkeypatch.setattr(app_module, "_run_subprocess", fake_run_subprocess)

    app = create_app(settings)
    client = TestClient(app)
    client.run_history = run_history  # type: ignore[attr-defined]
    return client


def test_create_list_delete_sandbox(monkeypatch, tmp_path):
    client = build_test_app(monkeypatch, tmp_path)

    response = client.post(
        "/api/sandboxes",
        json={
            "image": "ghcr.io/openhands/agent-server:latest",
            "platform": "linux/amd64",
            "environment": {"DEBUG": "1"},
            "ports": [{"container": 8000, "host": 41001}],
            "mounts": [
                {
                    "source": str(tmp_path),
                    "target": "/workspace",
                    "read_only": False,
                }
            ],
            "command": ["--host", "0.0.0.0", "--port", "8000"],
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["container_id"] == "container123"
    assert data["ports"][0]["host"] == 41001

    list_response = client.get("/api/sandboxes")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    sandbox_id = data["sandbox_id"]
    delete_response = client.delete(f"/api/sandboxes/{sandbox_id}")
    assert delete_response.status_code == 204

    # Ensure docker run was invoked with expected arguments
    run_commands = client.run_history  # type: ignore[attr-defined]
    assert any(cmd[0:2] == ["docker", "run"] for cmd in run_commands)


def test_reject_unapproved_image(monkeypatch, tmp_path):
    client = build_test_app(monkeypatch, tmp_path)

    response = client.post(
        "/api/sandboxes",
        json={
            "image": "ubuntu:latest",
            "ports": [{"container": 8000, "host": 41002}],
            "command": ["--host", "0.0.0.0", "--port", "8000"],
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Requested image is not in the allowed prefix list"
