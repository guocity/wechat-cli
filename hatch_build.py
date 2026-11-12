"""Reject releases with missing, damaged, or stale prebuilt macOS helpers."""

import hashlib
import json
import os
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        assets = Path(self.root) / "src/wechat_cli/backends/macos/assets"
        try:
            manifest = json.loads((assets / "bin/manifest.json").read_text())
            expected = {"native-reader.dylib", "channels-native-v2.dylib", "native-controls", "silk.dylib"}
            assert set(manifest["files"]) == expected
            assert manifest["architecture"] == "arm64"
            for name, entry in manifest["files"].items():
                assert hashlib.sha256((assets / "bin" / name).read_bytes()).hexdigest() == entry["sha256"]
            for name in ["bridge.cpp", "channels-native.cpp", "controls.swift"]:
                assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == manifest["sources"][name]
            assert (assets / "bin/SILK-LICENSE.txt").is_file()
        except (OSError, ValueError, KeyError, AssertionError) as error:
            raise RuntimeError("Run tools/build_macos_native.py on Apple Silicon before packaging") from error

    def finalize(self, version, build_data, artifact_path):
        if os.name != "nt" or self.target_name != "wheel" or version != "standard":
            return
        # A Windows checkout cannot represent the Unix execute bit in stat().
        # Preserve it in the universal wheel without changing any RECORD hashes.
        wheel = Path(artifact_path)
        controls = "wechat_cli/backends/macos/assets/bin/native-controls"
        with TemporaryDirectory(dir=wheel.parent) as directory:
            staged = Path(directory) / wheel.name
            with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(staged, "w") as target:
                for entry in source.infolist():
                    data = source.read(entry)
                    if entry.filename == controls:
                        entry.create_system = 3
                        entry.external_attr |= 0o111 << 16
                    target.writestr(entry, data)
            staged.replace(wheel)
