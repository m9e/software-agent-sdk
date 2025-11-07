"""HTTP client for the sandbox controller service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable

import httpx

from .schema import CreateSandboxRequest, CreateSandboxResponse, PortBinding, SandboxInfo


@dataclass(slots=True)
class SandboxHandle:
    """Represents a sandbox managed by the controller."""

    sandbox_id: str
    container_id: str
    name: str
    ports: Dict[int, int]


class SandboxControllerClient:
    """Synchronous client for interacting with the sandbox controller API."""

    def __init__(self, base_url: str, *, timeout: float = 30.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SandboxControllerClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self.close()

    def create_sandbox(
        self,
        *,
        image: str,
        platform: str,
        ports: Iterable[PortBinding],
        environment: Dict[str, str],
        mounts: Iterable[dict],
        command: Iterable[str],
        name: str | None = None,
    ) -> SandboxHandle:
        request = CreateSandboxRequest(
            image=image,
            platform=platform,
            name=name,
            ports=list(ports),
            environment=environment,
            mounts=list(mounts),
            command=list(command),
        )
        response = self._client.post(
            "/api/sandboxes",
            json=request.model_dump(),
        )
        response.raise_for_status()
        payload = CreateSandboxResponse.model_validate(response.json())
        return SandboxHandle(
            sandbox_id=payload.sandbox_id,
            container_id=payload.container_id,
            name=payload.name,
            ports={mapping.container: mapping.host for mapping in payload.ports},
        )

    def delete_sandbox(self, sandbox_id: str) -> None:
        response = self._client.delete(f"/api/sandboxes/{sandbox_id}")
        if response.status_code not in {200, 202, 204}:
            response.raise_for_status()

    def get_sandbox(self, sandbox_id: str) -> SandboxHandle | None:
        response = self._client.get(f"/api/sandboxes/{sandbox_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        info = SandboxInfo.model_validate(response.json())
        return SandboxHandle(
            sandbox_id=info.sandbox_id,
            container_id=info.container_id,
            name=info.name,
            ports={mapping.container: mapping.host for mapping in info.ports},
        )
