"""Build an opt-in Channels research copy; never alter the source or global policy.

Adds debugging/library-validation exceptions to AppEx's development helpers.
Requires an existing, supported wechat-cli prepared app. Does not register or
launch the result. The user must explicitly choose this research runtime.
"""

import argparse
import hashlib
import json
import os
import plistlib
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from wechat_cli.backends.macos.channels import RENDERER
from wechat_cli.backends.macos.channels_native import HELPER
from wechat_cli.backends.macos.native_assets import native_asset
from wechat_cli.backends.macos.setup import check_payload

RENDERER_SHA256 = "54e5542ed950e98092407982da638cc44f178910c53b879d925cf8e5edc8a52c"


def execute(*args):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True)


def add_loader(binary):
    """Keep UUID, code addresses and OS minimum; replace build-tool metadata only."""
    data = bytearray(binary.read_bytes())
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("Expected the validated universal renderer")
    for i in range(struct.unpack_from(">I", data, 4)[0]):
        _, _, base, size, _ = struct.unpack_from(">IIIII", data, 8 + 20 * i)
        count, original_size = struct.unpack_from("<II", data, base + 16)
        cursor, first = base + 32, base + size
        commands = []
        for _ in range(count):
            command, length = struct.unpack_from("<II", data, cursor)
            chunk = bytes(data[cursor : cursor + length])
            if command == 0x19:
                for section in range(struct.unpack_from("<I", data, cursor + 64)[0]):
                    offset = struct.unpack_from("<I", data, cursor + 72 + section * 80 + 48)[0]
                    if offset:
                        first = min(first, base + offset)
            if command == 0x32:
                platform, minimum, sdk = struct.unpack_from("<III", chunk, 8)
                if platform != 1:
                    raise ValueError("Expected a macOS renderer")
                chunk = struct.pack("<IIII", 0x24, 16, minimum, sdk)
            commands.append(chunk)
            cursor += length
        commands.append((struct.pack("<IIIIII", 12, 40, 24, 0, 0, 0) + b"@loader_path/g\0").ljust(40, b"\0"))
        payload = b"".join(commands)
        end = base + 32 + len(payload)
        if end > first or any(data[base + 32 + original_size : end]):
            raise ValueError("Insufficient safe Mach-O header space")
        length = max(original_size, len(payload))
        data[base + 32 : base + 32 + length] = payload.ljust(length, b"\0")
        struct.pack_into("<II", data, base + 16, len(commands), len(payload))
    binary.write_bytes(data)


def prepare(source, destination):
    source, destination = source.resolve(), destination.resolve()
    if destination.exists() or destination == source or source in destination.parents:
        raise ValueError("Choose a new, separate destination")
    profile = check_payload(source)
    if hashlib.sha256((source / RENDERER).read_bytes()).hexdigest() != RENDERER_SHA256:
        raise ValueError("Unsupported renderer fingerprint; refusing to patch")
    if not (source / "Contents/Frameworks/FridaGadget.dylib").is_file():
        raise ValueError("Source must be an existing wechat-cli prepared app")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="channels-prepare-", dir=destination.parent) as temporary:
        root = Path(temporary) / "WeChatChannelsResearch.app"
        execute("/usr/bin/ditto", source, root)
        binary = root / RENDERER
        renderer = binary.parents[2]
        appex = root / "Contents/MacOS/WeChatAppEx.app"
        framework = appex / "Contents/Frameworks/WeChatAppEx Framework.framework"
        shutil.copyfile(root / "Contents/Frameworks/FridaGadget.dylib", binary.parent / "g")
        config = binary.parent / "g.config"
        config.write_text(
            json.dumps(
                {
                    "code_signing": "required",
                    "interaction": {
                        "type": "listen",
                        "address": "127.0.0.1",
                        "port": 45200,
                        "on_port_conflict": "pick-next",
                        "token": secrets.token_hex(32),
                        "on_load": "resume",
                    },
                }
            )
        )
        config.chmod(0o600)
        native_helper = binary.parent / HELPER
        shutil.copyfile(native_asset("channels-native-v2.dylib"), native_helper)
        execute("/usr/bin/codesign", "--force", "--sign", "-", native_helper)
        add_loader(binary)
        for item in [binary.parent / "g", config]:
            execute("/usr/bin/codesign", "--force", "--sign", "-", item)
        helpers = sorted((framework / "Versions/C/Helpers").glob("*.app"))
        for index, helper in enumerate(helpers + [appex]):
            result = execute("/usr/bin/codesign", "-d", "--entitlements", ":-", helper)
            entitlements = plistlib.loads(result.stdout)
            entitlements["com.apple.security.cs.disable-library-validation"] = True
            if helper == renderer:
                entitlements["com.apple.security.get-task-allow"] = True
            path = Path(temporary) / f"entitlements-{index}.plist"
            path.write_bytes(plistlib.dumps(entitlements))
            execute(
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                "--options",
                "runtime",
                "--entitlements",
                path,
                helper,
            )
        for item in [framework, appex, root]:
            execute(
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                "--preserve-metadata=identifier,entitlements,flags",
                item,
            )
        execute("/usr/bin/codesign", "--verify", "--deep", "--strict", root)
        os.rename(root, destination)
    return {
        "app": str(destination),
        "version": profile["version"],
        "build": profile["build"],
        "signatureVerified": True,
        "registered": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This research build supports macOS only")
    print(json.dumps(prepare(args.source, args.destination), indent=2))


if __name__ == "__main__":
    main()
