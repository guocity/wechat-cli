import base64
import io

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image

from wechat_cli.core.errors import AppError
from wechat_cli.core.media import (
    cdn_url,
    decode_media,
    decode_upload,
    mime_type,
    prepare_voice,
    silk_duration,
    validate_image,
)


def test_xor_and_aes_media_decoding():
    data = b"GIF89a" + bytes(42)
    assert decode_media(bytes(x ^ 0x89 for x in data)) == data
    key = bytes(range(16))
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    assert decode_media(encrypted, key.hex()) == data
    assert decode_media(b"unknown") is None
    assert decode_media(b"#!AMR\n\x02#!SILK_V3data") == b"\x02#!SILK_V3data"


@pytest.mark.parametrize("value", ["", "a", "====", "aGVsbG8=\n", "aGVsbG8", None, 4])
def test_upload_rejects_noncanonical_base64(value):
    with pytest.raises(AppError):
        decode_upload(value)


def test_upload_bounds():
    assert decode_upload("aGVsbG8=") == b"hello"
    with pytest.raises(AppError):
        decode_upload("aGVsbG8=", 4)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "https://emoji.qpic.cn.evil.test/x",
        "https://emoji.qpic.cn:443/x",
        "https://user@emoji.qpic.cn/x",
        "file:///etc/passwd",
        "https://[::1]/x",
    ],
)
def test_cdn_blocks_unsupported_urls(url):
    with pytest.raises(AppError):
        cdn_url(url)


def test_cdn_upgrades_known_alias():
    assert cdn_url("http://snsvideo.c2c.wechat.com/a?b=c") == "https://socwxsns.video.qq.com/a?b=c"


def test_ocr_validates_real_image():
    output = io.BytesIO()
    Image.new("RGB", (32, 16)).save(output, "PNG")
    assert validate_image(output.getvalue()) == {"width": 32, "height": 16}
    with pytest.raises(AppError):
        validate_image(b"not png")
    assert mime_type(output.getvalue()) == "image/png"


def test_silk_frame_validation_and_voice_preparation():
    silk = b"\x02#!SILK_V3" + (b"\x04\x00abcd" * 50)
    assert silk_duration(silk) == 1000
    result = prepare_voice(
        {"chatId": "filehelper", "format": "silk", "dataBase64": base64.b64encode(silk).decode()}
    )
    assert result["durationMs"] == 1000
    for data in [b"not silk", silk[:-1], b"#!SILK_V3\x01\x00a", silk + b"\x02\x00x"]:
        with pytest.raises(AppError):
            silk_duration(data)


def channel_message(url="https://finder.video.qq.com/video", cover="https://finder.video.qq.com/cover"):
    from wechat_cli.core.models import resolve_message

    return resolve_message(
        {
            "id": "42",
            "type": 49,
            "content": f"""<msg><appmsg><type>51</type>
      <finderFeed><objectId>123</objectId><mediaList><media><mediaType>4</mediaType>
      <url>{url}</url><coverUrl>{cover}</coverUrl></media></mediaList></finderFeed></appmsg></msg>""",
        }
    )


def test_channels_video_and_cover_are_separate(monkeypatch):
    from wechat_cli.core import media

    mp4 = b"\x00\x00\x00\x10ftypisom\x00\x00\x00\x00" + b"\x00\x00\x00\x08moov\x00\x00\x00\x08mdat"
    calls = []

    def download(url, **kwargs):
        calls.append(url)
        return (mp4, "../../original.mp4") if url.endswith("/video") else (b"GIF89a" + bytes(42), None)

    monkeypatch.setattr(media, "download_media", download)
    assert media.read_channels(channel_message(), "full") == (mp4, "video/mp4", "original.mp4")
    assert media.read_channels(channel_message(), "preview")[1] == "image/gif"
    assert calls == ["https://finder.video.qq.com/video", "https://finder.video.qq.com/cover"]


def test_channels_rejects_encrypted_or_truncated_video(monkeypatch):
    from wechat_cli.core import media

    for data in [b"GIF89aimage", b"encrypted", b"\x00\x00\x00\x40ftypisom"]:
        monkeypatch.setattr(media, "download_media", lambda *a, **kw: (data, None))
        with pytest.raises(AppError, match="encrypted or incomplete"):
            media.read_channels(channel_message(), "full")


def test_channels_requires_video_media_and_safe_filename():
    from wechat_cli.core.media import channels_filename, read_channels

    with pytest.raises(AppError, match="no downloadable"):
        read_channels(channel_message(url=""), "full")
    assert channels_filename(None, "123") == "channels-123.mp4"
    assert channels_filename("C:\\private\\clip.mp4", "123") == "clip.mp4"
    assert channels_filename("x\r\n.mp4", "123") == "x__.mp4"
