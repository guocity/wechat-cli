"""Maintainer-only build of Apple Silicon binaries. Requires Xcode Command Line Tools."""

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/wechat_cli/backends/macos/assets"
SILK_URL = "https://files.pythonhosted.org/packages/66/bb/938dd697b6bc2d851ffec4ffe82ed20078e58bd0ef049e84f4d038d6f991/pilk-0.2.4.tar.gz"
SILK_SHA256 = "d4a1bcf93dc6ef5e95e0cfd728ed4ef4d49f9c0476d70816fecbe456cc762e7f"


def run(*args):
    subprocess.run(list(map(str, args)), check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--silk-archive", type=Path, help="Verified pilk 0.2.4 source archive (offline build)"
    )
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("Build on Apple Silicon macOS")
    if args.silk_archive:
        archive = args.silk_archive.read_bytes()
    else:
        with urllib.request.urlopen(SILK_URL, timeout=60) as response:
            archive = response.read(1024 * 1024)
    if hashlib.sha256(archive).hexdigest() != SILK_SHA256:
        raise ValueError("SILK source checksum mismatch")
    output = ASSETS / "bin"
    with tempfile.TemporaryDirectory(prefix="wechat-native-") as temporary:
        work = Path(temporary)
        # Only the Skype-licensed SDK is used; no pilk wrappers or Python extension.
        sdk = work / "silk"
        sdk.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar.getmembers():
                parts = Path(member.name).parts
                if (
                    len(parts) == 4
                    and parts[1] == "src"
                    and parts[2] in {"SKP_SILK_SRC", "interface"}
                    and member.isfile()
                    and parts[3].endswith((".c", ".h"))
                ):
                    target = sdk / parts[2] / parts[3]
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(tar.extractfile(member).read())
        common = ["-arch", "arm64", "-mmacosx-version-min=13.0", "-O2"]
        for source, name in [
            ("bridge.cpp", "native-reader.dylib"),
            ("channels-native.cpp", "channels-native-v2.dylib"),
        ]:
            run(
                "/usr/bin/clang++",
                *common,
                "-std=c++17",
                "-dynamiclib",
                "-Wall",
                "-Wextra",
                "-Werror",
                ASSETS / source,
                "-Wl,-install_name,@rpath/" + name,
                "-o",
                work / name,
            )
        run(
            "/usr/bin/swiftc",
            "-target",
            "arm64-apple-macos13.0",
            "-O",
            "-module-cache-path",
            work / "swift-cache",
            ASSETS / "controls.swift",
            "-o",
            work / "native-controls",
        )
        (work / "silk-assert.c").write_text(
            "#include <stdlib.h>\nvoid SKP_assert(int condition) { if (!condition) abort(); }\n"
        )
        run(
            "/usr/bin/clang",
            *common,
            "-dynamiclib",
            "-fwrapv",
            "-I",
            sdk / "interface",
            "-I",
            sdk / "SKP_SILK_SRC",
            *sorted((sdk / "SKP_SILK_SRC").glob("*.c")),
            work / "silk-assert.c",
            "-Wl,-install_name,@rpath/silk.dylib",
            "-o",
            work / "silk.dylib",
        )
        header = (sdk / "interface/SKP_Silk_SDK_API.h").read_text()
        (work / "SILK-LICENSE.txt").write_text(header[: header.index("#ifndef")].strip() + "\n")
        names = ["native-reader.dylib", "channels-native-v2.dylib", "native-controls", "silk.dylib"]
        manifest = {
            "architecture": "arm64",
            "minimumMacOS": "13.0",
            "silkSourceSha256": SILK_SHA256,
            "sources": {
                n: hashlib.sha256((ASSETS / n).read_bytes()).hexdigest()
                for n in ["bridge.cpp", "channels-native.cpp", "controls.swift"]
            },
            "files": {},
        }
        for name in names:
            run("/usr/bin/codesign", "--force", "--sign", "-", work / name)
            run("/usr/bin/codesign", "--verify", "--strict", work / name)
            manifest["files"][name] = {"sha256": hashlib.sha256((work / name).read_bytes()).hexdigest()}
        output.mkdir(exist_ok=True)
        for name in [*names, "SILK-LICENSE.txt"]:
            shutil.copy2(work / name, output / name)
        # Publish the manifest last so an interrupted build cannot validate mixed assets.
        (work / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.replace(work / "manifest.json", output / "manifest.json")
    print(f"Built and signed macOS arm64 runtime assets in {output}")


if __name__ == "__main__":
    main()
