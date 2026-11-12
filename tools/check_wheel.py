"""Validate the distributable package contains its runtime assets and no vendor app or private state."""

import argparse
import email
import hashlib
import json
import re
import zipfile
from pathlib import Path

NATIVE_ROOT = "wechat_cli/backends/macos/assets/bin/"
NATIVE_FILES = {"native-reader.dylib", "channels-native-v2.dylib", "silk.dylib", "native-controls"}
COMPATIBILITY = Path(__file__).resolve().parents[1] / "src/wechat_cli/compatibility"

REQUIRED = {
    "wechat_cli/core/dependencies.py",
    "wechat_cli/backends/windows/assets/agent.js",
    "wechat_cli/backends/windows/assets/transcription.js",
    "wechat_cli/backends/windows/media_cache.py",
    "wechat_cli/backends/windows/native_tools.py",
    "wechat_cli/backends/windows/ocr.py",
    "wechat_cli/backends/windows/crypto.py",
    "wechat_cli/backends/windows/images.py",
    "wechat_cli/backends/windows/protobuf.py",
    "wechat_cli/backends/windows/worker.py",
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
}


def check_compatibility(archive):
    root = "wechat_cli/compatibility/"
    catalog = {
        name.removeprefix(root): archive.read(name)
        for name in archive.namelist()
        if name.startswith(root) and name.endswith(".json")
    }
    source = {
        path.relative_to(COMPATIBILITY).as_posix(): path.read_bytes()
        for path in COMPATIBILITY.rglob("*.json")
    }
    assert catalog == source, "Wheel compatibility data differs from the checkout"

    for platform, architecture in (("macos", "arm64"), ("windows", "x64")):
        manifest = json.loads(catalog[f"{platform}.json"])
        prefix = f"{platform}-{architecture}/"
        assert manifest["profile"].startswith(prefix) and manifest["profile"] in catalog, manifest
        assert manifest["installerUrl"].startswith("https://"), manifest
        if platform == "macos":
            hashes = set()
            for name, data in catalog.items():
                if not name.startswith(prefix):
                    continue
                profile = json.loads(data)
                version, build, digest = (profile[key] for key in ("version", "build", "payloadSha256"))
                assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version) and re.fullmatch(r"[0-9]+", build), name
                assert name == f"{prefix}{version}-{build}.json" and profile["arch"] == architecture, name
                assert re.fullmatch(r"[0-9a-f]{64}", digest) and digest not in hashes, name
                hashes.add(digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.wheel) as archive:
        names = set(archive.namelist())
        assert REQUIRED <= names, REQUIRED - names
        check_compatibility(archive)
        assert {NATIVE_ROOT + n for n in NATIVE_FILES | {"manifest.json", "SILK-LICENSE.txt"}} <= names
        manifest = json.loads(archive.read(NATIVE_ROOT + "manifest.json"))
        for name in NATIVE_FILES:
            data = archive.read(NATIVE_ROOT + name)
            assert data[:4] == b"\xcf\xfa\xed\xfe", name
            assert hashlib.sha256(data).hexdigest() == manifest["files"][name]["sha256"], name
        controls = archive.getinfo(NATIVE_ROOT + "native-controls")
        assert (controls.external_attr >> 16) & 0o111, "Controls helper must be executable"
        assert not any(
            ".app/" in n
            or (n.endswith((".dylib", ".exe", ".dll")) and n not in {NATIVE_ROOT + p for p in NATIVE_FILES})
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
        assert sum(i.file_size for i in archive.infolist()) < 6 * 1024**2
        print(f"{args.wheel.name}: assets, entry point, metadata and private-state exclusion passed")


if __name__ == "__main__":
    main()
