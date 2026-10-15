"""Prepare a verified portable vendor copy; never rewrite its signed PE files."""

import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import tempfile
import urllib.request
import zlib
from pathlib import Path, PurePosixPath

from wechat_cli.core.config import PACKAGE, private_directory, save_json
from wechat_cli.core.errors import AppError
from wechat_cli.core.network import ssl_context

RELEASE = json.loads((PACKAGE / "compatibility/windows.json").read_text(encoding="utf-8"))
PROFILE = json.loads((PACKAGE / "compatibility" / RELEASE["profile"]).read_text(encoding="utf-8"))
WECHAT_URL = RELEASE["installerUrl"]


def require_windows():
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise AppError("unsupported_platform", "Windows setup requires Windows x64", 424)


def fingerprint(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def powershell(script, *, timeout=120):
    import base64

    try:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                base64.b64encode(script.encode("utf-16le")).decode(),
            ],
            capture_output=True,
            check=True,
            timeout=timeout,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise AppError("windows_command_failed", "Windows verification or app launch failed", 424) from None


def verify_signature(path):
    literal = str(path).replace("'", "''")
    powershell(
        "$ErrorActionPreference='Stop'; "
        f"$s=Get-AuthenticodeSignature -LiteralPath '{literal}'; "
        "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch "
        "'^CN=Tencent Technology \\(Shenzhen\\) Company Limited,') {exit 1}"
    )


def check_app(value):
    app = Path(value).expanduser().resolve()
    if app.is_file():
        app = app.parent
    executable = app / "Weixin.exe"
    payload = app / PROFILE["version"] / "Weixin.dll"
    try:
        if (
            fingerprint(executable) != PROFILE["executableSha256"]
            or fingerprint(payload) != PROFILE["payloadSha256"]
        ):
            raise AppError(
                "unsupported_wechat_version",
                "This Windows payload has no validated native profile. Update wechat-cli or select a supported copy.",
                424,
            )
    except OSError:
        raise AppError(
            "invalid_wechat_app", "Expected Weixin.exe and the supported version directory", 424
        ) from None
    return {**PROFILE, "app": str(app), "executable": str(executable), "payload": str(payload)}


def download(url, destination):
    if not url.lower().startswith("https://"):
        raise AppError("invalid_download_url", "An HTTPS installer URL is required")
    if destination.exists() and fingerprint(destination) == RELEASE["installerSha256"]:
        return destination
    temporary = destination.with_suffix(".part")
    try:
        with (
            urllib.request.urlopen(url, context=ssl_context(), timeout=60) as response,
            temporary.open("wb") as output,
        ):
            if not response.url.lower().startswith("https://"):
                raise AppError("invalid_download_url", "Installer redirected away from HTTPS")
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > 512 * 1024**2:
                    raise AppError("download_too_large", "Installer exceeded the download limit", 424)
                output.write(block)
        if fingerprint(temporary) != RELEASE["installerSha256"]:
            raise AppError(
                "unsupported_wechat_version",
                "Tencent installer changed; update wechat-cli before preparing this build",
                424,
            )
        os.replace(temporary, destination)
    except (OSError, TimeoutError):
        raise AppError(
            "download_failed",
            "Cannot download the verified WeChat installer; check the connection and retry",
            424,
        ) from None
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def embedded_archive(installer, destination):
    # This exact signed NSIS build stores its complete 7z payload unchanged.
    data = installer.read_bytes()
    offset = data.find(b"7z\xbc\xaf\x27\x1c")
    if offset < 0 or offset + 32 > len(data):
        raise AppError("invalid_installer", "Missing supported embedded archive", 424)
    crc, relative, size = struct.unpack_from("<IQQ", data, offset + 8)
    end = offset + 32 + relative + size
    if zlib.crc32(data[offset + 12 : offset + 32]) != crc or end > len(data):
        raise AppError("invalid_installer", "Invalid embedded archive bounds", 424)
    payload = memoryview(data)[offset:end]
    if hashlib.sha256(payload).hexdigest() != RELEASE["archiveSha256"]:
        raise AppError("invalid_installer", "Unexpected installer payload", 424)
    destination.write_bytes(payload)


def extract(archive, destination):
    try:
        listing = subprocess.run(
            ["tar.exe", "-tf", str(archive)], capture_output=True, check=True, encoding="utf-8", timeout=60
        ).stdout
        for line in listing.splitlines():
            path = PurePosixPath(line.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or ":" in line:
                raise AppError("invalid_installer", "Unsafe archive member", 424)
        subprocess.run(
            ["tar.exe", "-xf", str(archive), "-C", str(destination)],
            capture_output=True,
            check=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        raise AppError(
            "windows_extraction_failed",
            "Cannot extract WeChat; Windows tar.exe with 7z support is required",
            424,
        ) from None


def load_runtime(directory):
    try:
        value = json.loads((directory / "native-runtime.json").read_text(encoding="utf-8"))
        if value.get("backend") != "windows" or not Path(value["app"]).is_absolute():
            raise ValueError()
        return value
    except (OSError, ValueError, KeyError):
        raise AppError(
            "setup_required", "Run wechat-cli setup to prepare a Windows WeChat copy", 424
        ) from None


def setup(
    directory, *, app=None, source=None, destination=None, gadget=None, wechat_url=WECHAT_URL, publish=True
):
    require_windows()
    if gadget:
        raise AppError("invalid_options", "Windows uses Frida attach; --gadget is macOS-only")
    private_directory(directory)
    if app:
        info = check_app(app)
        verify_signature(Path(info["executable"]))
    else:
        target = (
            Path(destination).expanduser().resolve()
            if destination
            else directory / "apps" / f"WeChat-{PROFILE['version']}"
        )
        if target.exists():
            if destination:
                raise AppError("destination_exists", "Choose a new destination or use --app")
            info = check_app(target)
        else:
            if source:
                original = Path(check_app(source)["app"])
                if target == original or original in target.parents or target in original.parents:
                    raise AppError("invalid_destination", "Destination must be separate from the source app")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".wechat-setup-", dir=target.parent) as temp:
                stage = Path(temp) / "app"
                if source:
                    shutil.copytree(original, stage)
                else:
                    cache = private_directory(directory / "downloads")
                    installer = download(wechat_url, cache / f"WeChat-{PROFILE['version']}.exe")
                    verify_signature(installer)
                    archive = Path(temp) / "install.7z"
                    embedded_archive(installer, archive)
                    stage.mkdir()
                    extract(archive, stage)
                info = check_app(stage)
                verify_signature(Path(info["executable"]))
                os.rename(stage, target)
            info = check_app(target)
    runtime = {
        "backend": "windows",
        "app": info["app"],
        "version": info["version"],
        "payloadSha256": info["payloadSha256"],
    }
    if publish:
        save_json(directory / "native-runtime.json", runtime)
    return {**runtime, "ready": True, "binaryModified": False}


def doctor(directory):
    require_windows()
    info = check_app(load_runtime(directory)["app"])
    verify_signature(Path(info["executable"]))
    import frida

    return {
        "ready": True,
        "backend": "windows",
        "version": info["version"],
        "app": info["app"],
        "frida": frida.__version__,
        "binaryModified": False,
    }


def ensure_current(directory):
    from wechat_cli.host import lock_file

    from .processes import check_instance

    with lock_file(directory / "upgrade.lock"):
        old = load_runtime(directory)
        if old.get("version") == PROFILE["version"]:
            check_app(old["app"])
            return old
        check_instance(None)
        prepared = setup(directory, publish=False)
        check_instance(None)
        save_json(directory / "previous-native-runtime.json", old)
        save_json(directory / "native-runtime.json", prepared)
        return prepared
