"""Validate the distributable package contains its runtime assets and no vendor app or private state."""

import argparse
import email
import zipfile
from pathlib import Path

REQUIRED = {
    "wechat_cli/core/dependencies.py",
    "wechat_cli/compatibility/windows.json",
    "wechat_cli/compatibility/windows-x64/4.1.15.53.json",
    "wechat_cli/backends/windows/assets/agent.js",
    "wechat_cli/backends/windows/assets/transcription.js",
    "wechat_cli/backends/windows/media_cache.py",
    "wechat_cli/backends/windows/native_tools.py",
    "wechat_cli/backends/windows/ocr.py",
    "wechat_cli/backends/windows/crypto.py",
    "wechat_cli/backends/windows/images.py",
    "wechat_cli/backends/windows/protobuf.py",
    "wechat_cli/backends/windows/worker.py",
    "wechat_cli/compatibility/macos.json",
    "wechat_cli/cli.py",
    "wechat_cli/backends/macos/assets/gui-lifecycle.js",
    "wechat_cli/core/contracts.py",
    "wechat_cli/runtime/transport.py",
    "wechat_cli/api/app.py",
    "wechat_cli/backends/macos/worker.py",
    "wechat_cli/backends/macos/assets/native-agent.js",
    "wechat_cli/backends/macos/assets/startup-notice.js",
    "wechat_cli/backends/macos/assets/bridge.cpp",
    "wechat_cli/backends/macos/assets/controls.swift",
    "wechat_cli/web/index.html",
    "wechat_cli/web/app.js",
    "wechat_cli/web/style.css",
    "wechat_cli/compatibility/macos-arm64/4.1.13.34-269602.json",
    "wechat_cli/compatibility/macos-arm64/4.1.13-269631.json",
    "wechat_cli/compatibility/macos-arm64/4.1.15-270135.json",
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
            or n.endswith((".dylib", ".exe", ".dll"))
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
