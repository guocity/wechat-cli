import base64
import io
import math
import platform
import struct
import wave
from pathlib import Path

import pytest

from wechat_cli.core.media import codec, prepare_voice, silk_duration, voice_preview

pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin"
    or platform.machine() != "arm64"
    or not (Path(__file__).parents[1] / "src/wechat_cli/backends/macos/assets/bin/silk.dylib").is_file(),
    reason="Bundled Apple Silicon SILK codec is unavailable",
)


def test_real_silk_round_trip_and_wav_input():
    pcm = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / 24000))) for n in range(24000)
    )
    silk = codec("encode", pcm, 24000)
    assert silk_duration(silk) == 1000
    wav = voice_preview(silk)
    with wave.open(io.BytesIO(wav), "rb") as decoded:
        assert decoded.getnchannels() == 1
        assert decoded.getsampwidth() == 2
        assert decoded.getframerate() == 24000
        assert decoded.getnframes() == 24000
    result = prepare_voice(
        {"chatId": "filehelper", "format": "wav", "dataBase64": base64.b64encode(wav).decode()}
    )
    assert result["durationMs"] == 1000


@pytest.mark.parametrize("rate", [8000, 12000, 16000, 24000, 32000, 44100, 48000])
def test_bundled_codec_supports_each_sample_rate_without_compiler(rate, monkeypatch):
    monkeypatch.setenv("DEVELOPER_DIR", "/nonexistent")
    monkeypatch.setenv("SDKROOT", "/nonexistent")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    pcm = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * n / rate))) for n in range(rate))
    silk = codec("encode", pcm, rate)
    assert silk_duration(silk) == 1000
    assert len(codec("decode", silk, rate)) == len(pcm)


@pytest.mark.parametrize("data", [b"not silk", b"#!SILK_V3\x02\x00x", b"#!SILK_V3\xff\x7f"])
def test_bundled_decoder_rejects_malformed_packets(data):
    from wechat_cli.core.errors import AppError

    with pytest.raises(AppError, match="Audio conversion failed"):
        codec("decode", data)
