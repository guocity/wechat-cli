"""Validate uploads before opening WeChat's full, interactive Moments composer."""

import base64
import binascii
import io
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from wechat_cli.core.config import private_directory
from wechat_cli.core.errors import AppError

MAX_TOTAL = 64 * 1024**2
MAX_IMAGE = 8 * 1024**2


def stage_media(directory, payload):
    from PIL import Image

    kind, media = payload["kind"], payload["media"]
    if kind not in {"images", "video"} or not 1 <= len(media) <= (9 if kind == "images" else 1):
        raise AppError("invalid_moment_media", "Choose 1–9 images or one video of at most 30 seconds")
    root = private_directory(directory / "outgoing-moments")
    retained = 0
    for path in root.glob("moment-*"):
        if path.is_dir() and not path.is_symlink():
            if time.time() - path.stat().st_mtime > 3600:
                shutil.rmtree(path)
            else:
                retained += 1
    if retained >= 8:
        raise AppError(
            "moment_staging_full", "Finish existing Moments drafts and wait for uploads to expire", 429
        )
    folder = Path(tempfile.mkdtemp(prefix="moment-", dir=root))
    total = 0
    try:
        for index, item in enumerate(media):
            encoded = item["dataBase64"]
            limit = MAX_IMAGE if kind == "images" else MAX_TOTAL
            if len(encoded) > ((limit + 2) // 3) * 4:
                raise AppError("file_too_large", "Images must be at most 8 MiB; video at most 64 MiB", 413)
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise AppError("invalid_moment_media", "Invalid base64 media") from None
            total += len(data)
            if not data or len(data) > limit or total > MAX_TOTAL:
                raise AppError("file_too_large", "Moments uploads must total 1 byte to 64 MiB", 413)
            suffix = ".mp4"
            if kind == "images":
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        if image.format not in {"JPEG", "PNG"} or getattr(image, "n_frames", 1) != 1:
                            raise ValueError()
                        if image.width * image.height > 40_000_000:
                            raise ValueError()
                        suffix = ".jpg" if image.format == "JPEG" else ".png"
                        image.load()
                except Exception:
                    raise AppError(
                        "invalid_moment_media", "Use valid, still JPEG or PNG images up to 40 megapixels"
                    ) from None
            path = folder / f"{index + 1:02}{suffix}"
            path.write_bytes(data)
            path.chmod(0o600)
        return folder
    except Exception:
        shutil.rmtree(folder)
        raise


def prepare_moment(controls, payload):
    if not controls.lock.acquire(blocking=False):
        raise AppError("native_control_busy", "Another control action is running", 409)
    folder = None
    invoked = False
    try:
        if controls.moment_session:
            raise AppError("moment_composer_busy", "Finish or cancel the active Moments composer", 409)
        # Check before expiring old staged media: a draft surviving a service
        # restart may still reference those files.
        controls.invoke("moments.ready")
        folder = stage_media(controls.directory, payload)
        if payload["kind"] == "video":
            controls.invoke("moments.inspect", filePath=str(folder / "01.mp4"))
        invoked = True
        result = controls.invoke(
            "moments.compose",
            mediaDirectory=str(folder),
            mediaCount=len(payload["media"]),
            kind=payload["kind"],
            text=payload["text"],
        )
        if result.get("status") != "composing" or result.get("source") != "native":
            raise AppError("native_invalid_response", "The native Moments composer was not verified", 502)
        controls.moment_session = str(uuid.uuid4())
        return {**result, "sessionId": controls.moment_session, "acknowledgedByServer": False}
    finally:
        # WeChat can read staged media asynchronously. Keep it for an hour once opened.
        if folder and not invoked:
            shutil.rmtree(folder)
        controls.lock.release()


def moment_action(controls, session_id, action):
    if not controls.lock.acquire(blocking=False):
        raise AppError("native_control_busy", "Another control action is running", 409)
    try:
        if not controls.moment_session or controls.moment_session != session_id:
            raise AppError("moment_session_not_found", "Open a Moments composer in this service first", 409)
        if action not in {"status", "post", "cancel", "emoji", "mention", "visibility", "ai", "add-media"}:
            raise AppError("invalid_input", "Unknown Moments action")
        # Never retry a final click after an uncertain result, even with a new key.
        if action == "post":
            controls.moment_session = None
        try:
            result = controls.invoke("moments." + action)
        except AppError as error:
            if action == "post" and getattr(error, "possible_send", True) is False:
                controls.moment_session = session_id
            raise
        if action == "cancel" or result.get("status") == "closed":
            controls.moment_session = None
        return {**result, "sessionId": session_id, "acknowledgedByServer": False}
    finally:
        controls.lock.release()


def post_moment(controls, payload):
    result = prepare_moment(controls, payload)
    return moment_action(controls, result["sessionId"], "post")
