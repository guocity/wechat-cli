"""Explicit backend selection; unsupported hosts never load macOS native code."""

import platform
import sys

from wechat_cli.core.dependencies import require_modules
from wechat_cli.core.errors import AppError


def platform_backend():
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        require_modules(("frida", "cryptography", "PIL"))
        from .macos import backend

        return backend
    if platform.system() == "Windows" and platform.machine().lower() in {"amd64", "x86_64"}:
        require_modules(("frida", "zstandard") if sys.version_info < (3, 14) else ("frida",))
        from .windows import backend

        return backend
    raise AppError(
        "unsupported_platform",
        f"No validated WeChat backend for {platform.system()} {platform.machine()}. Apple Silicon macOS and Windows x64 are currently supported.",
        424,
    )


def create_backend(directory):
    return platform_backend().Backend(directory)
