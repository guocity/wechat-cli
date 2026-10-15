"""Private machine configuration; never store runtime state in uv's disposable cache."""

import json
import os
import secrets
import tempfile
from pathlib import Path

from .errors import AppError

ASSETS = Path(__file__).parent / "assets"


def data_directory(value=None):
    return (
        Path(
            value
            or os.environ.get("WECHAT_CLI_DATA_DIR")
            or Path.home() / "Library/Application Support/wechat-cli"
        )
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


def load_runtime(directory):
    try:
        value = json.loads((directory / "native-runtime.json").read_text())
        app = Path(value["app"])
        library = value["supportLibrary"]
        if not app.is_absolute() or not isinstance(library, str):
            raise ValueError()
        if (
            Path(library).name != library
            or not library.startswith("native-reader-")
            or not library.endswith(".dylib")
        ):
            raise ValueError()
        if not (app / "Contents/Frameworks" / library).is_file():
            raise ValueError()
        return {"app": str(app.resolve()), "supportLibrary": library}
    except (OSError, ValueError, KeyError, TypeError):
        raise AppError(
            "native_setup_required", "Run wechat-cli setup --app /path/to/patched/WeChat.app", 424
        ) from None
