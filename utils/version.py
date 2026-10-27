"""Read versions without importing the host application."""

import json
import os
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


@dataclass(frozen=True)
class HostVersionInfo:
    version: str
    source: str
    error: str = ""


def get_plugin_version() -> str:
    manifest = Path(__file__).resolve().parents[1] / "_manifest.json"
    return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])


def get_host_version_info() -> HostVersionInfo:
    injected = os.environ.get("MAIBOT_HOST_VERSION", "").strip()
    if injected:
        return HostVersionInfo(injected, "runner_environment")
    try:
        return HostVersionInfo(version("MaiBot"), "distribution")
    except PackageNotFoundError:
        return HostVersionInfo("unknown", "unavailable")
