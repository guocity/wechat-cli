import fcntl
import platform
from contextlib import contextmanager
from pathlib import Path

from wechat_cli.core.errors import AppError


def default_data_directory():
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/wechat-cli"
    return Path.home() / ".local/share/wechat-cli"


def detached_options():
    return {"start_new_session": True}


@contextmanager
def lock_file(path, blocking=True, *, instance=False):
    from wechat_cli.core.config import private_directory
    from wechat_cli.host import instance_error

    private_directory(path.parent)
    with path.open("a+") as handle:
        path.chmod(0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            if instance:
                raise instance_error() from None
            raise AppError(
                "server_already_running", "A server is already running for this data directory", 409
            ) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def private_permissions(path, *, directory=False):
    path.chmod(0o700 if directory else 0o600)
