"""FastAPI application for controlling OpenHands sandbox containers."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable

from fastapi import Depends, FastAPI, HTTPException, status

from .schema import (
    CreateSandboxRequest,
    CreateSandboxResponse,
    PortMapping,
    SandboxInfo,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SandboxSettings:
    """Configuration for the sandbox controller service."""

    allowed_image_prefixes: tuple[str, ...] = (
        os.getenv("SANDBOX_ALLOWED_IMAGE_PREFIXES")
        and tuple(
            prefix.strip()
            for prefix in os.getenv("SANDBOX_ALLOWED_IMAGE_PREFIXES", "").split(",")
            if prefix.strip()
        )
    ) or ("ghcr.io/openhands/",)
    allowed_mount_targets: tuple[str, ...] = (
        os.getenv("SANDBOX_ALLOWED_MOUNT_TARGETS")
        and tuple(
            target.strip()
            for target in os.getenv("SANDBOX_ALLOWED_MOUNT_TARGETS", "").split(",")
            if target.strip()
        )
    ) or ("/workspace",)
    port_min: int = int(os.getenv("SANDBOX_PORT_MIN", "30000"))
    port_max: int = int(os.getenv("SANDBOX_PORT_MAX", "39999"))
    name_prefix: str = os.getenv("SANDBOX_NAME_PREFIX", "openhands-sandbox-")
    max_command_args: int = int(os.getenv("SANDBOX_MAX_COMMAND_ARGS", "64"))

    def validate(self) -> None:
        if self.port_min >= self.port_max:
            msg = "SANDBOX_PORT_MIN must be less than SANDBOX_PORT_MAX"
            raise ValueError(msg)
        if any(
            not prefix or prefix.startswith("*") for prefix in self.allowed_image_prefixes
        ):
            logger.warning(
                "Using permissive image prefixes. Consider setting "
                "SANDBOX_ALLOWED_IMAGE_PREFIXES to an explicit allow list."
            )


@dataclass(slots=True)
class SandboxRecord:
    sandbox_id: str
    container_id: str
    name: str
    ports: Dict[int, int]

    def to_response(self) -> SandboxInfo:
        return SandboxInfo(
            sandbox_id=self.sandbox_id,
            container_id=self.container_id,
            name=self.name,
            ports=[
                PortMapping(container=container, host=host)
                for container, host in sorted(self.ports.items())
            ],
        )


@dataclass(slots=True)
class ControllerState:
    settings: SandboxSettings
    sandboxes: Dict[str, SandboxRecord] = field(default_factory=dict)
    allocated_ports: Dict[int, str] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _check_port_available(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _select_port(state: ControllerState, requested: int | None) -> int:
    settings = state.settings
    if requested is not None:
        if requested < settings.port_min or requested > settings.port_max:
            msg = f"Requested port {requested} outside allowed range"
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=msg)
        if requested in state.allocated_ports:
            msg = f"Requested port {requested} is already allocated"
            raise HTTPException(status.HTTP_409_CONFLICT, detail=msg)
        if not _check_port_available(requested):
            msg = f"Requested port {requested} is not available"
            raise HTTPException(status.HTTP_409_CONFLICT, detail=msg)
        return requested

    candidates = list(range(settings.port_min, settings.port_max + 1))
    random.shuffle(candidates)
    for port in candidates:
        if port in state.allocated_ports:
            continue
        if _check_port_available(port):
            return port
    msg = "No available ports in configured range"
    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=msg)


def _build_docker_run_command(
    *,
    request: CreateSandboxRequest,
    settings: SandboxSettings,
    assigned_ports: Dict[int, int],
    container_name: str,
) -> list[str]:
    if len(request.command) > settings.max_command_args:
        msg = "Command argument length exceeds configured limit"
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=msg)

    for prefix in settings.allowed_image_prefixes:
        if prefix and request.image.startswith(prefix):
            break
    else:
        msg = "Requested image is not in the allowed prefix list"
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=msg)

    for mount in request.mounts:
        if mount.target not in settings.allowed_mount_targets:
            msg = f"Mount target {mount.target} is not permitted"
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=msg)
        if not os.path.exists(mount.source):
            msg = f"Mount source {mount.source} does not exist"
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=msg)

    cmd: list[str] = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--platform",
        request.platform,
        "--name",
        container_name,
    ]

    for container_port, host_port in sorted(assigned_ports.items()):
        cmd.extend(["-p", f"{host_port}:{container_port}"])

    for key, value in request.environment.items():
        cmd.extend(["-e", f"{key}={value}"])

    for mount in request.mounts:
        mode = "ro" if mount.read_only else "rw"
        cmd.extend(["-v", f"{mount.source}:{mount.target}:{mode}"])

    cmd.append(request.image)
    cmd.extend(request.command)
    return cmd


async def _run_subprocess(command: Iterable[str]) -> subprocess.CompletedProcess[str]:
    cmd_list = list(command)
    logger.debug("Executing command: %s", " ".join(cmd_list))
    return await asyncio.to_thread(
        subprocess.run,
        cmd_list,
        capture_output=True,
        text=True,
    )


async def _stop_container(container_id: str) -> None:
    result = await _run_subprocess(["docker", "stop", container_id])
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "No such container" in stderr:
            logger.warning("Attempted to stop missing container %s", container_id)
            return
        logger.error(
            "Failed to stop container %s: %s", container_id, stderr or result.stdout
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop container: {stderr or result.stdout}",
        )


def create_app(settings: SandboxSettings | None = None) -> FastAPI:
    settings = settings or SandboxSettings()
    settings.validate()
    state = ControllerState(settings=settings)

    app = FastAPI(title="OpenHands Sandbox Controller", version="0.1.0")

    def get_state() -> ControllerState:
        return state

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/sandboxes")
    async def list_sandboxes(controller: ControllerState = Depends(get_state)) -> list[SandboxInfo]:
        async with controller.lock:
            return [record.to_response() for record in controller.sandboxes.values()]

    @app.get("/api/sandboxes/{sandbox_id}")
    async def get_sandbox(
        sandbox_id: str, controller: ControllerState = Depends(get_state)
    ) -> SandboxInfo:
        async with controller.lock:
            record = controller.sandboxes.get(sandbox_id)
            if record is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Sandbox not found")
            return record.to_response()

    @app.post(
        "/api/sandboxes",
        status_code=status.HTTP_201_CREATED,
        response_model=CreateSandboxResponse,
    )
    async def create_sandbox(
        request: CreateSandboxRequest, controller: ControllerState = Depends(get_state)
    ) -> CreateSandboxResponse:
        async with controller.lock:
            assigned_ports: Dict[int, int] = {}
            try:
                for binding in request.ports:
                    host_port = _select_port(controller, binding.host)
                    assigned_ports[binding.container] = host_port
                    controller.allocated_ports[host_port] = "reserved"
            except Exception:
                for port in assigned_ports.values():
                    controller.allocated_ports.pop(port, None)
                raise

            container_name = request.name or f"{settings.name_prefix}{uuid.uuid4().hex[:8]}"
            docker_cmd = _build_docker_run_command(
                request=request,
                settings=settings,
                assigned_ports=assigned_ports,
                container_name=container_name,
            )

            result = await _run_subprocess(docker_cmd)
            if result.returncode != 0:
                stderr = result.stderr.strip()
                stdout = result.stdout.strip()
                for port in assigned_ports.values():
                    controller.allocated_ports.pop(port, None)
                msg = stderr or stdout or "Failed to start container"
                logger.error("Docker run failed: %s", msg)
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)

            container_id = result.stdout.strip()
            sandbox_id = uuid.uuid4().hex
            record = SandboxRecord(
                sandbox_id=sandbox_id,
                container_id=container_id,
                name=container_name,
                ports=dict(assigned_ports),
            )
            controller.sandboxes[sandbox_id] = record
            for port in assigned_ports.values():
                controller.allocated_ports[port] = sandbox_id

            logger.info(
                "Created sandbox %s (%s) mapping ports %s",
                sandbox_id,
                container_name,
                assigned_ports,
            )

            return CreateSandboxResponse(
                sandbox_id=sandbox_id,
                container_id=container_id,
                name=container_name,
                ports=[
                    PortMapping(container=container, host=host)
                    for container, host in sorted(assigned_ports.items())
                ],
            )

    @app.delete("/api/sandboxes/{sandbox_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_sandbox(
        sandbox_id: str, controller: ControllerState = Depends(get_state)
    ) -> None:
        async with controller.lock:
            record = controller.sandboxes.pop(sandbox_id, None)
            if record is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Sandbox not found")
            for port in record.ports.values():
                controller.allocated_ports.pop(port, None)
        await _stop_container(record.container_id)
        logger.info("Deleted sandbox %s (%s)", sandbox_id, record.name)

    @app.on_event("shutdown")
    async def shutdown_cleanup() -> None:
        async with state.lock:
            sandboxes = list(state.sandboxes.values())
            state.sandboxes.clear()
            state.allocated_ports.clear()
        for record in sandboxes:
            try:
                await _stop_container(record.container_id)
            except HTTPException:
                logger.exception("Failed to stop container %s during shutdown", record.name)

    return app


__all__ = ["create_app", "SandboxSettings"]
