"""Pydantic models shared between the sandbox controller server and client."""

from __future__ import annotations

import re
from typing import Dict, List

from pydantic import BaseModel, Field, field_validator, model_validator


_IMAGE_RE = re.compile(r"^[a-zA-Z0-9._/@:-]+$")
_ENV_KEY_RE = re.compile(r"^[A-Z0-9_]+$")


class PortBinding(BaseModel):
    """Container port binding requested by the client."""

    container: int = Field(..., ge=1, le=65535)
    host: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="Host port to bind to. If None, the controller assigns one.",
    )

    @field_validator("container")
    @classmethod
    def _validate_container_port(cls, value: int) -> int:
        if value in {0, 1, 7, 9}:
            msg = "Reserved or unsupported container port"
            raise ValueError(msg)
        return value


class MountSpec(BaseModel):
    """File system mount description."""

    source: str = Field(..., min_length=1, description="Absolute host path")
    target: str = Field(..., min_length=1, description="Container path")
    read_only: bool = Field(default=False)

    @model_validator(mode="after")
    def _validate_paths(self) -> "MountSpec":
        if not self.source.startswith("/"):
            msg = "Mount source must be an absolute path"
            raise ValueError(msg)
        if ".." in self.target:
            msg = "Mount target cannot contain parent path segments"
            raise ValueError(msg)
        if ":" in self.source:
            msg = "Mount source cannot contain ':'"
            raise ValueError(msg)
        if not self.target.startswith("/"):
            msg = "Mount target must be an absolute path"
            raise ValueError(msg)
        return self


class CreateSandboxRequest(BaseModel):
    """Request payload for creating a sandbox container."""

    image: str = Field(..., description="Docker image to run")
    platform: str = Field(default="linux/amd64")
    name: str | None = Field(default=None)
    environment: Dict[str, str] = Field(default_factory=dict)
    ports: List[PortBinding] = Field(default_factory=list)
    mounts: List[MountSpec] = Field(default_factory=list)
    command: List[str] = Field(default_factory=list)

    @field_validator("image")
    @classmethod
    def _validate_image(cls, value: str) -> str:
        if not _IMAGE_RE.fullmatch(value):
            msg = "Image contains invalid characters"
            raise ValueError(msg)
        return value

    @field_validator("platform")
    @classmethod
    def _validate_platform(cls, value: str) -> str:
        if ".." in value or value.strip() == "":
            msg = "Platform must be a non-empty value"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_environment(self) -> "CreateSandboxRequest":
        for key, val in self.environment.items():
            if not _ENV_KEY_RE.fullmatch(key):
                msg = f"Invalid environment variable name: {key}"
                raise ValueError(msg)
            if "\n" in val or "\r" in val:
                msg = "Environment variable values cannot contain newlines"
                raise ValueError(msg)
        for arg in self.command:
            if arg.strip() == "":
                msg = "Command arguments must be non-empty"
                raise ValueError(msg)
            if "\n" in arg or "\r" in arg:
                msg = "Command arguments cannot contain newlines"
                raise ValueError(msg)
        return self


class PortMapping(BaseModel):
    """Host/container port mapping returned by the controller."""

    container: int = Field(..., ge=1, le=65535)
    host: int = Field(..., ge=1, le=65535)


class CreateSandboxResponse(BaseModel):
    """Response returned when a sandbox container is created."""

    sandbox_id: str = Field(..., description="Controller-issued sandbox identifier")
    container_id: str = Field(..., description="Docker container identifier")
    name: str = Field(..., description="Docker container name")
    ports: List[PortMapping] = Field(default_factory=list)


class SandboxInfo(BaseModel):
    """Information about a tracked sandbox."""

    sandbox_id: str
    container_id: str
    name: str
    ports: List[PortMapping]
