"""Validate the distributable package contains its runtime assets and no vendor app or private state."""

import argparse
import email
import zipfile
from pathlib import Path

REQUIRED = {
    "wechat_cli/cli.py",
    "wechat_cli/server.py",
    "wechat_cli/worker.py",
    "wechat_cli/assets/native-agent.js",
    "wechat_cli/assets/startup-notice.js",
    "wechat_cli/assets/bridge.cpp",
    "wechat_cli/assets/controls.swift",
    "wechat_cli/assets/index.html",
    "wechat_cli/assets/app.js",
    "wechat_cli/assets/style.css",
    "wechat_cli/assets/profiles/4.1.13.34-269602.json",
    "wechat_cli/assets/profiles/4.1.13-269631.json",
    "wechat_cli/assets/profiles/4.1.15-270135.json",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.wheel) as archive:
        names = set(archive.namelist())
        assert REQUIRED <= names, REQUIRED - names
        assert not any(
            ".app/" in n
            or n.endswith(".dylib")
            or "api-token" in n
            or "native-runtime.json" in n
            or ".venv/" in n
            or "__pycache__/" in n
            for n in names
        )
        metadata_path = next(n for n in names if n.endswith(".dist-info/METADATA"))
        metadata = email.message_from_bytes(archive.read(metadata_path))
        assert metadata["Name"] == "wechat-cli"
        assert metadata["Metadata-Version"] == "2.4"
        assert metadata["Requires-Python"] == ">=3.11"
        entries = archive.read(next(n for n in names if n.endswith(".dist-info/entry_points.txt"))).decode()
        assert "wechat-cli = wechat_cli.cli:main" in entries
        assert sum(i.file_size for i in archive.infolist()) < 2 * 1024**2
        print(f"{args.wheel.name}: assets, entry point, metadata and private-state exclusion passed")


if __name__ == "__main__":
    main()
