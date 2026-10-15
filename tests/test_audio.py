import base64
import importlib.util
import io
import math
import struct
import wave

import pytest

from wechat_cli.core.media import codec, prepare_voice, silk_duration, voice_preview

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pilk") is None,
    reason="SILK codec (pilk) is not installed",
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
