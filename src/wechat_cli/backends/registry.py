"""Explicit backend selection; unsupported hosts never load macOS native code."""

import platform

from wechat_cli.core.errors import AppError


def platform_backend():
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        from .macos import backend

        return backend
    raise AppError(
        "unsupported_platform",
        f"No validated WeChat backend for {platform.system()} {platform.machine()}. Apple Silicon macOS is currently supported; Windows support is under investigation.",
        424,
    )


def create_backend(directory):
    return platform_backend().MacOSBackend(directory)
