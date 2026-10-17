"""Download, validate, copy and instrument WeChat without modifying the installed app."""

import hashlib
import json
import lzma
import os
import platform
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from wechat_cli import __version__
from wechat_cli.core.config import PACKAGE, private_directory, save_json
from wechat_cli.core.errors import AppError
from wechat_cli.core.network import ssl_context

from .native_assets import native_asset

RELEASE = json.loads((PACKAGE / "compatibility/macos.json").read_text(encoding="utf-8"))
WECHAT_URL = RELEASE["installerUrl"]
GADGET_URL = (
    "https://github.com/frida/frida/releases/download/17.8.0/frida-gadget-17.8.0-macos-universal.dylib.xz"
)
GADGET_SHA256 = "4cf5433452e745e8b283c7a2e79518c2cd6295e85869589dc3c171d5689e6b8c"
GADGET_LOAD = b"@executable_path/../Frameworks/FridaGadget.dylib"
ASSETS = Path(__file__).parent / "assets"
PROFILES = PACKAGE / "compatibility/macos-arm64"


def require_macos():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise AppError("unsupported_platform", "Native setup requires Apple Silicon macOS", 424)


def execute(args, **kwargs):
    try:
        return subprocess.run(
            [str(x) for x in args],
            check=True,
            capture_output=True,
            timeout=kwargs.pop("timeout", 120),
            **kwargs,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        # Do not include command outputs; they may contain private paths or tokens.
        raise AppError(
            "setup_command_failed",
            f"{Path(str(args[0])).name} failed; check app write permissions and macOS system tools",
            424,
        ) from error


def fingerprint(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def check_payload(app):
    try:
        actual = fingerprint(app / "Contents/Resources/wechat.dylib")
    except OSError:
        raise AppError("invalid_wechat_app", "Missing Contents/Resources/wechat.dylib", 424) from None
    for path in sorted(PROFILES.glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        if actual == profile["payloadSha256"]:
            return profile
    raise AppError(
        "unsupported_wechat_version",
        "This WeChat payload has no validated native profile. Update wechat-cli, "
        "or select a supported installer with --wechat-url. Stock WeChat was not modified.",
        424,
    )


def check_app(app):
    app = Path(app).expanduser().resolve()
    if app == Path("/Applications/WeChat.app").resolve():
        raise AppError(
            "stock_app_protected", "Use --source to copy stock WeChat, or --app with a separate patched copy"
        )
    check_payload(app)
    try:
        config = json.loads((app / "Contents/Frameworks/FridaGadget.config").read_text(encoding="utf-8"))[
            "interaction"
        ]
        if (
            config["type"] != "listen"
            or config.get("address", "127.0.0.1") != "127.0.0.1"
            or type(config["port"]) is not int
            or not 1 <= config["port"] <= 65535
            or not isinstance(config["token"], str)
            or len(config["token"]) < 16
        ):
            raise ValueError()
        if not (app / "Contents/Frameworks/FridaGadget.dylib").is_file():
            raise ValueError()
    except (OSError, KeyError, TypeError, ValueError):
        raise AppError(
            "invalid_gadget_config", "A loopback-only authenticated Frida Gadget is required", 424
        ) from None
    return app


def download(url, target, maximum, expected=None):
    """Bounded, atomic HTTPS download; a failed download never replaces a cached file."""
    if not url.startswith("https://"):
        raise AppError("invalid_download_url", "Installer downloads require HTTPS")
    if target.is_file() and expected and fingerprint(target) == expected:
        return target
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": f"wechat-cli/{__version__}"})
        with (
            urllib.request.urlopen(request, timeout=60, context=ssl_context()) as response,
            temporary.open("wb") as output,
        ):
            if not response.url.startswith("https://"):
                raise AppError("invalid_download_url", "Download redirected away from HTTPS")
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise AppError("download_too_large", "Download exceeds the configured size limit", 413)
                output.write(chunk)
        if expected and fingerprint(temporary) != expected:
            raise AppError(
                "download_checksum_mismatch", "Downloaded Frida Gadget failed SHA-256 verification", 424
            )
        os.replace(temporary, target)
        return target
    except (OSError, urllib.error.URLError) as error:
        raise AppError(
            "download_failed",
            "Cannot download the installer; check connectivity or supply --source /path/to/WeChat.app",
            424,
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def mounted_app(dmg, directory):
    mount = Path(tempfile.mkdtemp(prefix="mount-", dir=directory))
    attached = False
    try:
        execute(
            ["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, dmg], timeout=180
        )
        attached = True
        apps = [p for p in mount.glob("*.app") if p.name in {"WeChat.app", "微信.app"}]
        if len(apps) != 1:
            raise AppError("invalid_installer", "Installer must contain one WeChat.app", 424)
        yield apps[0]
    finally:
        if attached:
            execute(["/usr/bin/hdiutil", "detach", mount], timeout=30)
        mount.rmdir()


def inject_gadget(binary):
    """Add LC_LOAD_DYLIB only in verified empty Mach-O header padding.

    No relocation or vendor payload modification. Support thin 64-bit and
    universal 32-bit fat headers; fail closed for other layouts.
    """
    data = bytearray(binary.read_bytes())
    if data[:4] == b"\xca\xfe\xba\xbe":
        count = struct.unpack_from(">I", data, 4)[0]
        if not 1 <= count <= 8 or len(data) < 8 + count * 20:
            raise AppError("invalid_macho", "Invalid universal Mach-O header", 424)
        slices = [struct.unpack_from(">IIIII", data, 8 + i * 20)[2:4] for i in range(count)]
    else:
        slices = [(0, len(data))]
    for base, size in slices:
        if base + size > len(data) or size < 32 or data[base : base + 4] != b"\xcf\xfa\xed\xfe":
            raise AppError("invalid_macho", "Expected a 64-bit Mach-O executable", 424)
        ncmds, cmdsize = struct.unpack_from("<II", data, base + 16)
        end = base + 32 + cmdsize
        if end > base + size:
            raise AppError("invalid_macho", "Load commands exceed the Mach-O slice", 424)
        cursor, found = base + 32, False
        first_section = base + size
        for _ in range(ncmds):
            if cursor + 8 > end:
                raise AppError("invalid_macho", "Truncated load command", 424)
            cmd, length = struct.unpack_from("<II", data, cursor)
            if length < 8 or cursor + length > end:
                raise AppError("invalid_macho", "Invalid load command length", 424)
            if cmd == 0x19:
                if length < 72:
                    raise AppError("invalid_macho", "Truncated segment command", 424)
                section_count = struct.unpack_from("<I", data, cursor + 64)[0]
                if length < 72 + section_count * 80:
                    raise AppError("invalid_macho", "Truncated section table", 424)
                for section in range(section_count):
                    section_offset = struct.unpack_from("<I", data, cursor + 72 + section * 80 + 48)[0]
                    if section_offset:
                        first_section = min(first_section, base + section_offset)
            if cmd in {0xC, 0x80000018} and GADGET_LOAD in data[cursor : cursor + length]:
                found = True
            cursor += length
        if cursor != end:
            raise AppError("invalid_macho", "Inconsistent load command size", 424)
        if found:
            continue
        length = (24 + len(GADGET_LOAD) + 1 + 7) & ~7
        if end + length > first_section or any(data[end : end + length]):
            raise AppError("macho_padding_unavailable", "No empty header padding for Frida Gadget", 424)
        command = struct.pack("<IIIIII", 0xC, length, 24, 0, 0, 0) + GADGET_LOAD + b"\0"
        data[end : end + length] = command.ljust(length, b"\0")
        struct.pack_into("<II", data, base + 16, ncmds + 1, cmdsize + length)
    binary.write_bytes(data)


def build_helper(app):
    # Gadget configuration lives in Frameworks, so codesign treats it as nested code.
    # A generic file signature is stored in extended attributes by macOS.
    execute(["/usr/bin/codesign", "--force", "--sign", "-", app / "Contents/Frameworks/FridaGadget.config"])
    source = native_asset("native-reader.dylib")
    digest = fingerprint(source)[:16]
    library = f"native-reader-{digest}.dylib"
    output = app / "Contents/Frameworks" / library
    if not output.exists() or fingerprint(output) != fingerprint(source):
        temporary = output.with_suffix(f".{os.getpid()}.tmp")
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    execute(
        ["/usr/bin/codesign", "--force", "--sign", "-", "--entitlements", ASSETS / "entitlements.plist", app]
    )
    return library


def prepared_configuration(app, library, profile, directory, publish):
    runtime = {"app": str(app), "supportLibrary": library}
    if publish:
        save_json(directory / "native-runtime.json", runtime)
    return {"configured": True, **runtime, "version": profile["version"], "build": profile["build"]}


def expected_profile(app, expected_payload=None):
    profile = check_payload(app)
    if expected_payload is not None and profile["payloadSha256"] != expected_payload:
        raise AppError(
            "unexpected_wechat_payload",
            "The installer does not contain the expected validated WeChat release. Update wechat-cli and retry; the previous bridge configuration was preserved.",
            424,
        )
    return profile


def register(app, directory, *, publish=True, expected_payload=None):
    app = check_app(app)
    profile = expected_profile(app, expected_payload)
    library = build_helper(app)
    return prepared_configuration(app, library, profile, directory, publish)


def prepare(source, destination, gadget, directory, *, publish=True, expected_payload=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination == Path("/Applications/WeChat.app").resolve() or destination == source:
        raise AppError("stock_app_protected", "Destination must be a separate app copy")
    if source in destination.parents:
        raise AppError("invalid_destination", "Destination cannot be inside the source app")
    if destination.exists():
        raise AppError("destination_exists", "Destination already exists; use setup --app to register it")
    profile = expected_profile(source, expected_payload)  # Reject before copying hundreds of megabytes.
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=destination.parent) as staging:
        app = Path(staging) / "WeChatBridge.app"
        execute(["/usr/bin/ditto", source, app], timeout=180)
        frameworks = app / "Contents/Frameworks"
        shutil.copyfile(gadget, frameworks / "FridaGadget.dylib")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        save_json(
            frameworks / "FridaGadget.config",
            {
                "interaction": {
                    "type": "listen",
                    "address": "127.0.0.1",
                    "port": port,
                    "token": secrets.token_hex(32),
                    "on_load": "wait",
                }
            },
        )
        inject_gadget(app / "Contents/MacOS/WeChat")
        execute(["/usr/bin/codesign", "--force", "--sign", "-", frameworks / "FridaGadget.dylib"])
        library = build_helper(app)
        # Ensure no partially prepared app is exposed as the configured runtime.
        os.rename(app, destination)
    return prepared_configuration(destination, library, profile, directory, publish)


def setup(
    directory,
    *,
    app=None,
    source=None,
    destination=None,
    gadget=None,
    wechat_url=WECHAT_URL,
    publish=True,
    expected_payload=None,
):
    require_macos()
    private_directory(directory)
    if app:
        return register(app, directory, publish=publish, expected_payload=expected_payload)
    destination = Path(destination or directory / "apps/WeChatBridge.app").expanduser().resolve()
    if not source and destination.exists():
        return register(destination, directory, publish=publish, expected_payload=expected_payload)
    downloads = private_directory(directory / "downloads")
    if gadget:
        gadget_path = Path(gadget).expanduser().resolve()
    else:
        compressed = download(GADGET_URL, downloads / "gadget-17.8.0.dylib.xz", 64 * 1024**2, GADGET_SHA256)
        gadget_path = downloads / "FridaGadget.dylib"
        with lzma.open(compressed) as input_file, gadget_path.open("wb") as output:
            # The compressed archive is pinned to a verified hash.
            shutil.copyfileobj(input_file, output)
    if source:
        return prepare(
            source, destination, gadget_path, directory, publish=publish, expected_payload=expected_payload
        )
    dmg = download(wechat_url, downloads / "WeChat.dmg", 1024**3)
    with mounted_app(dmg, downloads) as stock:
        # Verify Tencent's signature before trusting any downloaded executable.
        execute(["/usr/bin/codesign", "--verify", "--deep", "--strict", stock])
        details = subprocess.run(
            ["/usr/bin/codesign", "-dv", "--verbose=4", str(stock)], capture_output=True, timeout=30
        )
        if b"TeamIdentifier=5A4RE8SF68" not in details.stderr:
            raise AppError(
                "invalid_vendor_signature",
                "Downloaded app is not signed by the expected WeChat developer",
                424,
            )
        return prepare(
            stock, destination, gadget_path, directory, publish=publish, expected_payload=expected_payload
        )


def doctor(directory):
    require_macos()
    runtime = load_runtime(directory)
    check_app(runtime["app"])
    import frida

    return {
        "ready": True,
        "app": runtime["app"],
        "version": check_payload(Path(runtime["app"]))["version"],
        "build": check_payload(Path(runtime["app"]))["build"],
        "fridaVersion": frida.__version__,
        "dataDir": str(directory),
    }


def load_runtime(directory):
    try:
        value = json.loads((directory / "native-runtime.json").read_text(encoding="utf-8"))
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
