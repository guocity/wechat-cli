"""Private machine configuration; never store runtime state in uv's disposable cache."""

import json
import os
import secrets
import tempfile
from pathlib import Path

from wechat_cli.core.errors import AppError
from wechat_cli.host import default_data_directory

PACKAGE = Path(__file__).resolve().parents[1]
WEB = PACKAGE / "web"


def data_directory(value=None):
    return (
        Path(value or os.environ.get("WECHAT_CLI_DATA_DIR") or default_data_directory())
        .expanduser()
        .resolve()
    )


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as output:
            output.write(value)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def save_json(path, value):
    private_write(path, json.dumps(value, indent=2) + "\n")


def api_token(directory):
    private_directory(directory)
    path = directory / "api-token"
    # Exclusive creation keeps concurrent CLI invocations on the same token.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as output:
            output.write(secrets.token_hex(32) + "\n")
    token = path.read_text().strip()
    if not 16 <= len(token) <= 512 or any(c.isspace() for c in token):
        raise AppError("invalid_api_token", f"Invalid API token in {path}", 424)
    path.chmod(0o600)
    return token
