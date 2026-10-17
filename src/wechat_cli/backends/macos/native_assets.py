"""Locate verified release binaries; never invoke a compiler on the user's machine."""

import hashlib
import json
from pathlib import Path

from wechat_cli.core.errors import AppError

BINARIES = Path(__file__).parent / "assets" / "bin"


def native_asset(name):
    try:
        manifest = json.loads((BINARIES / "manifest.json").read_text())
        expected = manifest["files"][name]["sha256"]
        if Path(name).name != name:
            raise ValueError()
        path = BINARIES / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError()
        return path
    except (OSError, KeyError, TypeError, ValueError):
        raise AppError(
            "native_asset_missing",
            "Bundled macOS native helper is missing or damaged. Reinstall wechat-cli; "
            "source maintainers must run tools/build_macos_native.py before packaging.",
            424,
        ) from None
