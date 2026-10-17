"""Publish through WeChat's existing authenticated Channels page and signed task runner.

No standalone Tencent HTTP protocol or arbitrary JavaScript endpoint is exposed.
Requires the explicitly prepared Channels research runtime, just like playback.
"""

import base64
import binascii
import json
import mmap
import os
import re
import socket
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from wechat_cli.core.errors import AppError
from wechat_cli.core.media import complete_mp4

from .channels import RENDERER
from .channels_native import HELPER
from .processes import process_snapshot
from .setup import ASSETS, load_runtime

MAX_VIDEO = 64 * 1024**2
MAX_LOCAL_VIDEO = 256 * 1024**2
IDENTIFY = """JSON.stringify({match:typeof location!=='undefined' &&
 location.origin==='https://channels.weixin.qq.com' &&
 ['/platform/post/create','/micro/content/post/create'].includes(location.pathname)})"""
ERRORS = {
    "channels_existing_draft": "Finish the existing Channels draft before starting another upload.",
    "channels_dialog_open": "Review and close the dialog in WeChat; the API does not accept agreements.",
    "channels_upload_not_ready": "WeChat has not enabled Publish; inspect upload processing or validation.",
    "channels_session_not_found": "This page no longer contains the requested upload session.",
    "channels_submission_already_requested": "Submission was already requested. Inspect Channels before retrying.",
    "channels_form_changed": "The Channels form changed or is not fully loaded. No action was taken.",
}


@contextmanager
def publishing_page(app):
    import frida

    expected = (Path(app) / RENDERER).resolve()
    helper = expected.with_name(HELPER)
    try:
        config = json.loads(expected.with_name("g.config").read_text())
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
            "channels_native_runtime_required", "Channels publishing needs the prepared research runtime", 424
        ) from None
    pids = {
        int(fields[0])
        for line in process_snapshot().splitlines()
        if len(fields := line.strip().split(None, 1)) == 2
        and fields[0].isdigit()
        and Path(fields[1]).resolve() == expected
    }
    source = (
        "const CHANNELS_THREAD_NAME='CrRendererMain';const CHANNELS_HELPER="
        + json.dumps(str(helper))
        + ";\n"
        + (ASSETS / "channels-agent.js").read_text()
    )
    manager = frida.get_device_manager()
    connections, matches = [], []
    try:
        for candidate in range(port, port + 16):
            try:
                with socket.create_connection(("127.0.0.1", candidate), timeout=0.1):
                    pass
            except OSError:
                continue
            endpoint = f"127.0.0.1:{candidate}"
            device = session = script = None
            try:
                device = manager.add_remote_device(endpoint, token=token)
                connections.append([endpoint, None, None])
                session = device.attach("Gadget")
                connections[-1][1] = session
                script = session.create_script(source)
                connections[-1][2] = script
                script.load()
                rpc = script.exports_sync
                identity = rpc.identity()
                if (
                    identity["pid"] not in pids
                    or Path(identity["path"]).resolve() != expected
                    or identity["policy"] != "required"
                ):
                    continue
                for context in rpc.workers():
                    if json.loads(rpc.evaluate(context, IDENTIFY)).get("match"):
                        matches.append((rpc, context))
            except Exception:
                continue
        if len(matches) != 1:
            raise AppError(
                "channels_page_not_found" if not matches else "channels_page_ambiguous",
                "Open exactly one Channels video publishing page inside the prepared WeChat app.",
                409,
            )
        rpc, context = matches[0]

        def call(command):
            source = "const CHANNELS_COMMAND=" + json.dumps(command) + ";\n"
            source += (ASSETS / "channels-options.js").read_text() + "\n"
            # Each evaluation gets a lexical wrapper so const declarations never collide.
            source = (
                "(()=>{" + source + "return (\n" + (ASSETS / "channels-publisher.js").read_text() + "\n)})()"
            )
            try:
                result = json.loads(rpc.evaluate(context, source))
            except Exception:
                raise AppError(
                    "channels_operation_interrupted",
                    "Channels operation was interrupted. Inspect WeChat before starting another request.",
                    502,
                ) from None
            if result.get("error"):
                code = result["error"]
                raise AppError(code, ERRORS.get(code, code.replace("_", " ")), 409)
            return result

        yield call
    finally:
        for endpoint, session, script in reversed(connections):
            for cleanup in (
                lambda: script.unload() if script else None,
                lambda: session.detach() if session else None,
                lambda: manager.remove_remote_device(endpoint),
            ):
                try:
                    cleanup()
                except Exception:
                    pass


def decode_video(encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise AppError("invalid_channel_video", "Use base64-encoded MP4 bytes") from None
    if not data or len(data) > MAX_VIDEO:
        raise AppError("file_too_large", "Channels API videos must be 1 byte to 64 MiB", 413)
    if not complete_mp4(data):
        raise AppError(
            "invalid_channel_video", "Use a complete MP4 file; WeChat validates codecs and duration"
        )
    return data


@contextmanager
def video_source(payload):
    if payload is None:
        yield None, None
        return
    if payload.get("dataBase64") is not None:
        yield decode_video(payload["dataBase64"]), "video.mp4"
        return
    path = Path(payload["videoPath"])
    if not path.is_absolute() or path.suffix.lower() != ".mp4":
        raise AppError("invalid_channel_video", "Use an absolute path to an MP4 file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        raise AppError("channel_video_unreadable", "Cannot open the local video file") from None
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_LOCAL_VIDEO:
            raise AppError(
                "file_too_large", "Local Channels videos must be regular files of 1 byte to 256 MiB", 413
            )
        with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if not complete_mp4(data):
                raise AppError("invalid_channel_video", "Use a complete MP4 file")
            yield data, path.name


def operate(backend, payload=None, *, post=False, session_id=None, page_factory=publishing_page):
    with video_source(payload) as (data, name):
        return _operate(
            backend, payload, data, name, post=post, session_id=session_id, page_factory=page_factory
        )


def _operate(backend, payload, data, name, *, post, session_id, page_factory):
    if not backend.channels_lock.acquire(blocking=False):
        raise AppError("native_control_busy", "Another Channels operation is running", 409)
    try:
        app = load_runtime(backend.directory)["app"]
        with page_factory(app) as call:
            if data is None:
                return call({"action": "post", "sessionId": session_id} if post else {"action": "status"})
            session_id = str(uuid.uuid4())
            call({"action": "begin", "sessionId": session_id})
            for offset in range(0, len(data), 48 * 1024):
                call(
                    {
                        "action": "chunk",
                        "sessionId": session_id,
                        "offset": offset,
                        "data": base64.b64encode(data[offset : offset + 48 * 1024]).decode(),
                    }
                )
            result = call(
                {
                    "action": "attach",
                    "sessionId": session_id,
                    "size": len(data),
                    "name": name,
                    "description": payload["description"],
                    "title": payload["title"],
                }
            )
            deadline = time.monotonic() + payload["waitSeconds"]
            while result["status"] == "processing" and time.monotonic() < deadline:
                time.sleep(0.5)
                result = call({"action": "status"})
            if payload.get("options"):
                result = apply_settings(call, session_id, {"options": payload["options"]})
            if post and result["status"] == "ready":
                return call({"action": "post", "sessionId": session_id})
            # Processing is resumable with /channels/publish, not a second upload.
            return result
    finally:
        backend.channels_lock.release()


def apply_settings(call, session_id, payload):
    if call({"action": "status"}).get("blockedByDialog"):
        raise AppError("channels_dialog_open", ERRORS["channels_dialog_open"], 409)

    def command(action, **extra):
        return call({"action": "options-" + action, "sessionId": session_id, **extra})

    text = {key: payload[key] for key in ("description", "title") if payload.get(key) is not None}
    if text:
        command("text", **text)
    options = payload.get("options") or {}
    for kind, clear in (
        ("location", "不显示位置"),
        ("activity", "不参与活动"),
        ("videoLabel", "No label needed"),
    ):
        if options.get(kind) is not None:
            search = options.get(kind + "Query")
            command("open", kind=kind, **({"query": search} if search is not None else {}))
            time.sleep(1 if search is not None else 0.3)
            deadline = time.monotonic() + 8
            while True:
                try:
                    command("select", kind=kind, value=options[kind] or clear)
                    break
                except AppError as error:
                    if error.code != "channels_option_not_found" or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
    link = options.get("link")
    if link is not None:
        before = call({"action": "status"})
        names = {"article": "公众号文章", "redEnvelope": "红包封面"}
        current = before.get("settings", {}).get("link", "")
        desired = names.get(link["type"])
        if (desired and current != desired) or (not desired and current in names.values()):
            command("open", kind="link")
            command("select", kind="link", value=desired or current)
        if desired:
            command("link-url", url=link["url"])
            deadline = time.monotonic() + 15
            while True:
                state = call({"action": "status"})
                validation = state.get("validation", {})
                if validation.get("linkError"):
                    raise AppError("channels_link_invalid", "WeChat rejected the supplied link", 409)
                if not validation.get("linkParsing"):
                    break
                if time.monotonic() >= deadline:
                    raise AppError(
                        "channels_link_not_ready", "Link validation is still running; inspect WeChat", 409
                    )
                time.sleep(0.5)
    schedule = options.get("scheduleAt")
    if schedule is not None:
        command("schedule-mode", enabled=schedule != "immediate")
        if schedule != "immediate":
            command("schedule-time", value=schedule)
    # Do this last: it may open terms that must be reviewed in the native page.
    if options.get("declareOriginal") is not None:
        command("original", value=options["declareOriginal"])
    if payload.get("nativeAction"):
        command("native", target=payload["nativeAction"])
    return call({"action": "status"})


def edit(backend, payload, *, choices=False, page_factory=publishing_page):
    if not backend.channels_lock.acquire(blocking=False):
        raise AppError("native_control_busy", "Another Channels operation is running", 409)
    try:
        with page_factory(load_runtime(backend.directory)["app"]) as call:
            if choices:
                command = {"action": "options-open", **payload}
                call(command)
                if payload.get("query") is not None:
                    time.sleep(1)
                return call(
                    {"action": "options-list", "sessionId": payload["sessionId"], "kind": payload["kind"]}
                )
            return apply_settings(call, payload["sessionId"], payload)
    finally:
        backend.channels_lock.release()
