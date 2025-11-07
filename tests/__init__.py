"""Test package configuration."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKSPACE_MEMBERS = [
    "sandbox-controller",
    "openhands-workspace",
    "openhands-sdk",
    "openhands-agent-server",
    "openhands-tools",
]


for member in _WORKSPACE_MEMBERS:
    path = _REPO_ROOT / member
    if not path.exists():
        continue
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

importlib.invalidate_caches()

if "openhands" in sys.modules:
    module = sys.modules["openhands"]
    if hasattr(module, "__path__"):
        for member in _WORKSPACE_MEMBERS:
            path = _REPO_ROOT / member / "openhands"
            if path.exists():
                path_str = str(path)
                if path_str not in module.__path__:
                    module.__path__.append(path_str)
