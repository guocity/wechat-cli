"""Host primitives only; a Windows WeChat backend is not yet validated."""

import msvcrt
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

from wechat_cli.core.errors import AppError


def default_data_directory():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "wechat-cli"


def detached_options():
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}


@contextmanager
def lock_file(path, blocking=True, *, instance=False):
    from wechat_cli.host import instance_error

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if not path.stat().st_size:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        except OSError:
            if instance:
                raise instance_error() from None
            raise AppError(
                "server_already_running", "A server is already running for this data directory", 409
            ) from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
