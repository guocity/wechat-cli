"""Small OS boundary. Import only the current host's implementation."""

import platform
from pathlib import Path

from wechat_cli.core.errors import AppError


def implementation():
    if platform.system() == "Windows":
        from . import windows

        return windows
    from . import posix

    return posix


def default_data_directory():
    return implementation().default_data_directory()


def lock_file(path: Path, blocking=True, *, instance=False):
    return implementation().lock_file(path, blocking=blocking, instance=instance)


def detached_options():
    return implementation().detached_options()


def instance_error():
    return AppError(
        "wechat_instance_conflict",
        "Only one WeChat instance is allowed. Quit the other WeChat app or stop its wechat-cli server, then retry.",
        409,
    )


def private_permissions(path, *, directory=False):
    implementation().private_permissions(path, directory=directory)
