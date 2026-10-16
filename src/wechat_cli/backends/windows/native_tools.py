"""Disposable processes for WeChat's own exported image and SILK codecs."""

import ctypes as ct
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

MAXIMUM = 32 * 1024**2


def library(app):
    runtime = Path(app) / "4.1.15.53"
    profile = json.loads((Path(__file__).parents[2] / "compatibility/windows-x64/4.1.15.53.json").read_text())
    with (runtime / "VoipEngine.dll").open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != profile["codecSha256"]:
            raise ValueError("unsupported codec build")
    directory = os.add_dll_directory(str(runtime))
    dll = ct.CDLL(str(runtime / "VoipEngine.dll"))
    dll._directory = directory
    return dll


def function(dll, name, result, arguments):
    method = getattr(dll, name)
    method.restype, method.argtypes = result, arguments
    return method


def audio(dll, action, data, rate):
    pointer, integer = ct.c_void_p, ct.c_int
    prefix = "SKP_Silk_SDK_"
    encode = action == "encode"
    title = "Encoder" if encode else "Decoder"
    size = integer()
    get_size = function(dll, prefix + "Get_" + title + "_Size", integer, [pointer])
    initialize = function(dll, prefix + "Init" + title, integer, [pointer, pointer] if encode else [pointer])
    if get_size(ct.byref(size)) != 0 or not 0 < size.value <= 1024**2:
        raise ValueError("invalid codec state size")
    state, control = ct.create_string_buffer(size.value), (integer * 32)()
    code = initialize(state, control) if encode else initialize(state)
    if code:
        raise ValueError("codec initialization failed")
    result = bytearray()
    output = ct.create_string_buffer(11520)
    length = ct.c_short()
    if encode:
        if rate not in {8000, 12000, 16000, 24000, 32000, 44100, 48000} or len(data) % 2:
            raise ValueError("invalid PCM format")
        if not 1 <= len(data) / (rate * 2) <= 60:
            raise ValueError("unsupported PCM duration")
        method = function(
            dll, prefix + "Encode", integer, [pointer, pointer, pointer, integer, pointer, pointer]
        )
        control[0], control[1], control[2], control[3] = rate, 24000, rate // 50, 24000
        control[5] = 2
        result.extend(b"\x02#!SILK_V3")
        chunk = rate // 50 * 2
        for offset in range(0, len(data), chunk):
            frame = data[offset : offset + chunk].ljust(chunk, b"\0")
            length.value = 2048
            if (
                method(state, control, ct.create_string_buffer(frame), chunk // 2, output, ct.byref(length))
                != 0
                or not 0 < length.value <= 2048
            ):
                raise ValueError("SILK encode failed")
            result.extend(struct.pack("<h", length.value))
            result.extend(output.raw[: length.value])
    else:
        if rate != 24000 or len(data) > 1024**2:
            raise ValueError("unsupported voice preview")
        offset = 10 if data.startswith(b"\x02#!SILK_V3") else 9 if data.startswith(b"#!SILK_V3") else -1
        if offset < 0:
            raise ValueError("invalid SILK header")
        method = function(
            dll, prefix + "Decode", integer, [pointer, pointer, integer, pointer, integer, pointer, pointer]
        )
        control[0] = rate
        packets = 0
        while offset < len(data):
            if offset + 2 > len(data):
                raise ValueError("truncated SILK packet")
            count = struct.unpack_from("<h", data, offset)[0]
            offset += 2
            if count == -1 and offset == len(data):
                break
            if not 0 < count <= 2048 or offset + count > len(data) or packets >= 3000:
                raise ValueError("invalid SILK packet")
            packets += 1
            packet = ct.create_string_buffer(data[offset : offset + count])
            offset += count
            for _ in range(5):
                length.value = 5760
                if method(state, control, 0, packet, count, output, ct.byref(length)) != 0:
                    raise ValueError("SILK decode failed")
                if not 0 < length.value <= 5760 or len(result) + length.value * 2 > rate * 2 * 60:
                    raise ValueError("invalid decoded voice duration")
                result.extend(output.raw[: length.value * 2])
                if not control[3]:
                    break
            else:
                raise ValueError("too many internal SILK frames")
        if not result:
            raise ValueError("empty voice")
    return bytes(result)


def render(dll, data):
    pointer, integer = ct.c_void_p, ct.c_int
    source = ct.create_string_buffer(data)
    info = (integer * 64)()
    inspect = function(dll, "wxam_dec_getWXGFInfo_5", integer, [pointer, integer, pointer])
    if inspect(source, len(data), info) != 0:
        raise ValueError("invalid wxgf image")
    width, height = info[0], info[1]
    if not 0 < width <= 16384 or not 0 < height <= 16384 or width * height > 16000000:
        raise ValueError("oversized image dimensions")
    length = integer(MAXIMUM)
    output = ct.create_string_buffer(MAXIMUM)
    options = (integer * 8)()
    convert = function(dll, "wxam_dec_wxam2pic_5", integer, [pointer, integer, pointer, pointer, pointer])
    if convert(source, len(data), output, ct.byref(length), options) != 0:
        raise ValueError("wxgf conversion failed")
    if not 0 < length.value <= MAXIMUM:
        raise ValueError("oversized wxgf result")
    result = output.raw[: length.value]
    if result.startswith(b"\xff\xd8"):
        end = result.rfind(b"\xff\xd9")
        if end < 0:
            raise ValueError("invalid JPEG output")
        result = result[: end + 2]
    return result


if __name__ == "__main__":
    app, action, source, destination, rate = sys.argv[1:]
    try:
        with Path(source).open("rb") as stream:
            data = stream.read(MAXIMUM + 1)
        if len(data) > MAXIMUM:
            raise ValueError("input too large")
        dll = library(app)
        result = render(dll, data) if action == "render" else audio(dll, action, data, int(rate))
        Path(destination).write_bytes(result)
    except Exception:
        raise SystemExit(1) from None
