"""Resolve a shared Channels card through WeChat's existing Finder worker.

No viewer/UI action and no decoded-buffer scan. The signed helper posts a normal
non-nestable task; native response identity and CDN boundaries are mandatory.
"""

import json
import re
import socket
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from wechat_cli.core.errors import AppError
from wechat_cli.core.media import cdn_url, complete_mp4, download_media
from wechat_cli.core.models import channels_descriptor

from .channels import RENDERER
from .channels_decode import decrypt
from .processes import process_snapshot
from .setup import ASSETS

HELPER = "channels-native-v2.dylib"


def playback_source(descriptor, object_id):
    if not isinstance(descriptor, dict) or descriptor.get("objectId") != object_id:
        raise AppError("native_media_identity_mismatch", "WeChat returned a different Channels card", 502)
    url, token, key = (descriptor.get(k) for k in ("url", "urlToken", "decodeKey"))
    if (
        not isinstance(key, str)
        or not re.fullmatch(r"\d{1,20}", key)
        or not 0 <= int(key) < 2**64
        or not isinstance(token, str)
        or not token.startswith(("&", "?"))
        or len(token) > 16384
        or any(ord(c) < 33 for c in token)
    ):
        raise AppError(
            "native_media_descriptor_missing", "WeChat did not return valid decoding metadata", 502
        )
    url = cdn_url(url)
    url += ("&" if "?" in url else "?") + token.lstrip("?&")
    format = descriptor.get("format")
    if format is not None:
        if not isinstance(format, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", format):
            raise AppError("native_media_descriptor_missing", "WeChat returned an invalid video format", 502)
        url += "&" + urlencode({"X-snsvideoflag": format})
    return cdn_url(url), int(key)


def decode_download(descriptor, object_id, downloader=download_media):
    url, key = playback_source(descriptor, object_id)
    encrypted, name = downloader(url, maximum=128 * 1024**2, with_name=True)
    data = encrypted if complete_mp4(encrypted) else decrypt(encrypted, key)
    if not complete_mp4(data):
        raise AppError(
            "channels_decode_failed", "WeChat's decoding metadata did not produce a valid MP4", 502
        )
    return data, name or descriptor.get("fileName")


def _resolve(reader, request, deadline):
    template = (ASSETS / "channels-resolver.js").read_text()

    def evaluate(worker, value):
        source = template.replace("__REQUEST__", json.dumps(value, ensure_ascii=True))
        return json.loads(reader.evaluate(worker, source))

    for worker in reader.workers():
        if time.monotonic() >= deadline:
            break
        if not evaluate(worker, {"operation": "identify"}).get("finder"):
            continue
        identifier = "wechat-cli-" + uuid.uuid4().hex
        try:
            result = evaluate(worker, {"operation": "start", "id": identifier, **request})
            if not result.get("started"):
                raise AppError(
                    "native_media_resolve_failed", "WeChat could not start native media resolution", 502
                )
            while time.monotonic() < deadline:
                result = evaluate(worker, {"operation": "poll", "id": identifier})
                if not result.get("pending"):
                    if result.get("error"):
                        raise AppError(
                            "native_media_resolve_failed", "WeChat could not resolve this Channels video", 502
                        )
                    return result
                time.sleep(0.05)
            raise AppError("native_media_timeout", "WeChat's native media request timed out", 504)
        finally:
            try:
                evaluate(worker, {"operation": "cancel", "id": identifier})
            except Exception:
                pass
    raise AppError("native_finder_worker_unavailable", "WeChat's background Finder worker is not ready", 424)


def resolve(app, message):
    import frida

    descriptor = channels_descriptor(message)
    object_id, nonce = descriptor.get("objectId"), descriptor.get("objectNonceId")
    if (
        not isinstance(object_id, str)
        or not re.fullmatch(r"\d{1,20}", object_id)
        or not isinstance(nonce, str)
        or not 1 <= len(nonce) <= 1024
    ):
        raise AppError("native_media_descriptor_missing", "This card lacks its native media identifier", 409)
    expected = (Path(app) / RENDERER).resolve()
    helper = expected.parent / HELPER
    try:
        config = json.loads((expected.parent / "g.config").read_text())
        interaction = config["interaction"]
        port, token = interaction["port"], interaction["token"]
        if (
            config.get("code_signing") != "required"
            or interaction.get("type") != "listen"
            or interaction.get("address") != "127.0.0.1"
            or type(port) is not int
            or not 1024 <= port <= 65504
            or not re.fullmatch(r"[a-f0-9]{64}", token)
            or not helper.is_file()
        ):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise AppError(
            "channels_native_runtime_required",
            "Update the approved Channels research app to enable native media loading",
            424,
        ) from None
    pids = {
        int(fields[0])
        for line in process_snapshot().splitlines()
        if len(fields := line.strip().split(None, 1)) == 2
        and fields[0].isdigit()
        and Path(fields[1]).resolve() == expected
    }
    manager = frida.get_device_manager()
    source = (
        "const CHANNELS_HELPER="
        + json.dumps(str(helper))
        + ";\n"
        + (ASSETS / "channels-agent.js").read_text()
    )
    deadline = time.monotonic() + 15
    for candidate in range(port, port + 16):
        if time.monotonic() >= deadline:
            break
        try:
            with socket.create_connection(("127.0.0.1", candidate), timeout=0.1):
                pass
        except OSError:
            continue
        endpoint = f"127.0.0.1:{candidate}"
        session = script = None
        try:
            device = manager.add_remote_device(endpoint, token=token)
            session = device.attach("Gadget")
            script = session.create_script(source)
            script.load()
            identity = script.exports_sync.identity()
            if (
                identity["pid"] not in pids
                or Path(identity["path"]).resolve() != expected
                or identity["policy"] != "required"
            ):
                continue
            return _resolve(script.exports_sync, {"objectId": object_id, "nonce": nonce}, deadline)
        except (
            frida.InvalidOperationError,
            frida.TransportError,
            frida.ProcessNotFoundError,
            frida.ServerNotRunningError,
            frida.TimedOutError,
            frida.PermissionDeniedError,
            frida.core.RPCException,
            ValueError,
            KeyError,
            TypeError,
        ):
            continue
        except AppError as error:
            if error.code != "native_finder_worker_unavailable":
                raise
        finally:
            for cleanup in (
                lambda: script.unload() if script else None,
                lambda: session.detach() if session else None,
                lambda: manager.remove_remote_device(endpoint),
            ):
                try:
                    cleanup()
                except Exception:
                    pass
    raise AppError(
        "native_finder_worker_unavailable",
        "WeChat's native Finder worker is unavailable; retry once WeChat has finished starting",
        424,
    )


def download(app, message):
    descriptor = resolve(app, message)
    return decode_download(descriptor, channels_descriptor(message)["objectId"])
