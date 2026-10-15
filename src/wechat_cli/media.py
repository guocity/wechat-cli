"""Message-scoped media access, bounded decoding, OCR and voice conversion."""

import base64
import hashlib
import io
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import wave
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image

from .errors import AppError
from .models import media_descriptor
from .network import ssl_context

MAX_MEDIA = 32 * 1024**2
CDN_HOSTS = {
    "wxapp.tc.qq.com",
    "emoji.qpic.cn",
    "mmbiz.qpic.cn",
    "emoji.qpic.tc.qq.com",
    "socwxsns.video.qq.com",
}


def decode_upload(encoded, maximum=8 * 1024**2):
    try:
        if not isinstance(encoded, str) or not encoded or len(encoded) > (maximum + 2) // 3 * 4:
            raise ValueError()
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > maximum or base64.b64encode(data).decode() != encoded:
            raise ValueError()
        return data
    except (ValueError, TypeError):
        raise AppError("invalid_input", "Supply canonical base64 within the upload limit") from None


def mime_type(data):
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    for header, mime in [
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"#!AMR\n", "audio/amr"),
        (b"OggS", "audio/ogg"),
        (b"#!SILK_V3", "audio/silk"),
        (b"\x02#!SILK_V3", "audio/silk"),
    ]:
        if data.startswith(header):
            return mime
    if data[:4] == b"RIFF":
        return {b"WEBP": "image/webp", b"WAVE": "audio/wav"}.get(data[8:12])
    if data[4:8] == b"ftyp":
        return "video/mp4"
    return None


def decode_media(data, key=None):
    if data.startswith(b"#!AMR\n") and mime_type(data[6:]) == "audio/silk":
        return data[6:]
    if mime_type(data):
        return data
    if not data:
        return None
    for header in [255, 137, 71, 82]:
        xor = data[0] ^ header
        probe = bytes(x ^ xor for x in data[:16])
        if (mime_type(probe) or "").startswith("image/"):
            return bytes(x ^ xor for x in data)
    if isinstance(key, str) and re.fullmatch(r"[a-fA-F0-9]{32}", key) and len(data) % 16 == 0:
        secret = bytes.fromhex(key)
        for mode in [modes.CBC(bytes(16)), modes.CBC(secret), modes.ECB()]:
            try:
                decryptor = Cipher(algorithms.AES(secret), mode).decryptor()
                plain = decryptor.update(data) + decryptor.finalize()
                unpad = padding.PKCS7(128).unpadder()
                plain = unpad.update(plain) + unpad.finalize()
                if mime_type(plain):
                    return plain
            except ValueError:
                pass
    return None


def cdn_url(raw):
    try:
        url = urlsplit(raw)
        hostname = url.hostname
        if hostname == "snsvideo.c2c.wechat.com":
            hostname = "socwxsns.video.qq.com"
        if (
            url.scheme not in {"http", "https"}
            or hostname not in CDN_HOSTS
            or url.port
            or url.username
            or url.password
        ):
            raise ValueError()
        return urlunsplit(("https", hostname, url.path, url.query, ""))
    except (ValueError, TypeError):
        raise AppError("media_url_denied", "Media URL is outside the supported WeChat CDN", 422) from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_media(raw):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ssl_context())
    )
    url = cdn_url(raw)
    for _ in range(3):
        try:
            with opener.open(url, timeout=10) as response:
                data = response.read(MAX_MEDIA + 1)
                if len(data) > MAX_MEDIA:
                    raise AppError("media_too_large", "Media exceeds the 32 MiB limit", 413)
                return data
        except urllib.error.HTTPError as error:
            if error.code in {301, 302, 303, 307, 308} and error.headers.get("Location"):
                url = cdn_url(urljoin(url, error.headers["Location"]))
                continue
            raise AppError("media_download_failed", "WeChat CDN could not return this media", 502) from None
        except OSError:
            raise AppError("media_download_failed", "Cannot reach the WeChat CDN", 502) from None
    raise AppError("media_download_failed", "Too many CDN redirects", 502)


def find_message(native, chat_id, identifier):
    message = next((m for m in native.messages(chat_id, 100)["items"] if m["id"] == identifier), None)
    if message is None:
        raise AppError("native_message_not_found", "Message is outside recent history", 404)
    return message


def read_media(native, chat_id, identifier, variant="full"):
    message = find_message(native, chat_id, identifier)
    if message["kind"] == "file":
        details = message["details"]
        size = details.get("sizeBytes", 0)
        if not 1 <= size <= 128 * 1024**2:
            raise AppError("media_too_large", "Attachment must be 1 byte through 128 MiB", 413)
        result = native.request("attachmentData", {"chatId": chat_id, "id": identifier})
        files = result.get("files", [])
        if len(files) > 1:
            raise AppError("native_attachment_ambiguous", "Multiple cached attachments match", 409)
        if not files:
            raise AppError("native_media_not_cached", "Download the attachment in WeChat first", 404)
        data = decode_upload(files[0]["dataBase64"], maximum=128 * 1024**2)
        if len(data) != size or details.get("md5") and hashlib.md5(data).hexdigest() != details["md5"]:
            raise AppError("native_attachment_invalid", "Cached attachment does not match its message", 422)
        mime = mime_type(data) or "application/octet-stream"
        if variant == "preview" and not mime.startswith("image/"):
            raise AppError("native_preview_unavailable", "Download this file to view it", 404)
        return data, mime, details.get("fileName", "attachment")
    if message["kind"] not in {"image", "voice", "video", "sticker"}:
        raise AppError("invalid_media_type", "This message has no supported media")
    descriptor = media_descriptor(message)
    if message["kind"] == "voice":
        result = native.request("voiceData", {"chatId": chat_id, "id": identifier})
        voice = next((m for m in result.get("items", []) if m["id"] == identifier), {})
        descriptor["md5"] = voice.get("nativeMediaFields", {}).get("voiceFile")

    def matches(data):
        mime = mime_type(data) or ""
        return (
            mime.startswith(("image/", "video/"))
            if message["kind"] == "sticker"
            else mime.startswith("audio/")
            if message["kind"] == "voice"
            else mime.startswith("video/") or variant == "preview" and mime.startswith("image/")
            if message["kind"] == "video"
            else mime.startswith("image/")
        )

    hashes = [
        descriptor.get("md5"),
        *descriptor["cacheKeys"],
        message["details"].get("media", {}).get("md5"),
        message.get("serverId"),
        message.get("id"),
    ]
    seen = set()
    for hashed in hashes:
        if not hashed or hashed in seen:
            continue
        seen.add(hashed)
        try:
            cached = native.request("cachedMedia", {"hash": hashed})
        except AppError as error:
            if error.code == "native_invalid_media_id":
                continue
            raise
        files = sorted(
            cached.get("files", []), key=lambda f: bool(f.get("isThumbnail")), reverse=variant == "preview"
        )
        for item in files:
            data = decode_media(decode_upload(item["dataBase64"], MAX_MEDIA), descriptor.get("key"))
            if data and matches(data):
                return (
                    voice_preview(data),
                    ("audio/wav" if mime_type(data) == "audio/silk" else mime_type(data)),
                    None,
                )
    remotes = [
        (descriptor.get("url"), descriptor.get("key")),
        (descriptor.get("encryptedUrl"), descriptor.get("key")),
        (descriptor.get("thumbnailUrl"), descriptor.get("thumbnailKey")),
    ]
    if variant == "preview":
        remotes = remotes[-1:] + remotes[:-1]
    for remote, key in remotes:
        if remote:
            data = decode_media(download_media(remote), key)
            if data and matches(data):
                return data, mime_type(data), None
    raise AppError("native_media_not_cached", "Open or download this media in WeChat, then retry", 404)


def validate_image(data):
    if not 1 <= len(data) <= 8 * 1024**2:
        raise AppError("image_too_large", "OCR images must be 1 byte through 8 MiB", 413)
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width > 16384 or height > 16384 or width * height > 16000000:
                raise AppError(
                    "image_dimensions_unsupported", "OCR images must be at most 16 megapixels", 413
                )
            if image.format not in {"PNG", "JPEG"}:
                raise ValueError()
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise AppError("invalid_image_format", "OCR supports valid PNG and JPEG images", 422) from None
    return {"width": width, "height": height}


def silk_duration(data):
    offset = 10 if data.startswith(b"\x02#!SILK_V3") else 9 if data.startswith(b"#!SILK_V3") else -1
    if offset < 0 or len(data) > 1048576:
        raise AppError("invalid_voice", "Invalid SILK header or size")
    frames = 0
    while offset < len(data):
        if offset + 2 > len(data):
            raise AppError("invalid_voice", "Truncated SILK frame")
        length = int.from_bytes(data[offset : offset + 2], "little", signed=True)
        offset += 2
        if length == -1 and offset == len(data):
            break
        if length <= 0 or offset + length > len(data) or frames >= 3000:
            raise AppError("invalid_voice", "Invalid SILK frame length")
        frames += 1
        offset += length
    duration = frames * 20
    if not 1000 <= duration <= 60000:
        raise AppError("invalid_voice", "Voice messages must last 1–60 seconds")
    return duration


def codec(action, data, rate=24000):
    try:
        import importlib.util

        if importlib.util.find_spec("pilk") is None:
            raise AppError(
                "audio_extra_required",
                "SILK codec is missing; reinstall wechat-cli with its dependencies",
                424,
            )
        with tempfile.TemporaryDirectory(prefix="wechat-audio-") as temporary:
            source, destination = Path(temporary) / "input", Path(temporary) / "output"
            source.write_bytes(data)
            subprocess.run(
                [sys.executable, "-m", "wechat_cli.codec", action, str(source), str(destination), str(rate)],
                check=True,
                timeout=15,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return destination.read_bytes()
    except (OSError, subprocess.SubprocessError):
        raise AppError("invalid_voice", "Audio conversion failed", 422) from None


def voice_preview(data):
    if mime_type(data) != "audio/silk":
        return data
    pcm = codec("decode", data)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)
    return output.getvalue()


def prepare_voice(input):
    if set(input) - {"chatId", "dataBase64", "format", "sampleRate"}:
        raise AppError("invalid_input", "Use chatId, dataBase64, format and optional sampleRate")
    data = decode_upload(input.get("dataBase64"))
    kind, rate = input.get("format"), input.get("sampleRate")
    if kind == "silk":
        if rate is not None:
            raise AppError("invalid_voice", "sampleRate applies only to raw PCM")
        duration = silk_duration(data)
    else:
        if kind == "wav" and rate is None:
            try:
                with wave.open(io.BytesIO(data), "rb") as wav:
                    if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                        raise ValueError()
                    rate = wav.getframerate()
                    data = wav.readframes(wav.getnframes())
            except (wave.Error, EOFError, ValueError):
                raise AppError("invalid_voice", "Use mono 16-bit PCM WAV") from None
        elif kind != "pcm_s16le":
            raise AppError("invalid_voice", "Use silk, wav, or pcm_s16le")
        if (
            type(rate) is not int
            or rate not in {8000, 12000, 16000, 24000, 32000, 44100, 48000}
            or len(data) % 2
        ):
            raise AppError("invalid_voice", "Invalid PCM sample rate or length")
        if not 1 <= len(data) / (rate * 2) <= 60:
            raise AppError("invalid_voice", "Voice messages must last 1–60 seconds")
        data = codec("encode", data, rate)
        duration = silk_duration(data)
    return {"chatId": input["chatId"], "dataBase64": base64.b64encode(data).decode(), "durationMs": duration}
