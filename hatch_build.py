"""Reject releases with missing, damaged, or stale prebuilt macOS helpers."""

import hashlib
import json
from pathlib import Path

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
