"""Isolated SILK SDK process, using a bundled library independent of Python's ABI."""

import ctypes as c
import struct
import sys
from pathlib import Path

from wechat_cli.backends.macos.native_assets import native_asset


class Encoder(c.Structure):
    _fields_ = [
        (name, c.c_int32)
        for name in ("rate", "internalRate", "packetSize", "bitRate", "loss", "complexity", "fec", "dtx")
    ]


class Decoder(c.Structure):
    _fields_ = [
        (name, c.c_int32) for name in ("rate", "frameSize", "framesPerPacket", "moreFrames", "fecOffset")
    ]


def convert(action, data, rate):
    if rate not in {8000, 12000, 16000, 24000, 32000, 44100, 48000} or action not in {"encode", "decode"}:
        raise ValueError("Invalid codec request")
    lib = c.CDLL(str(native_asset("silk.dylib")))
    void, integer, short = c.c_void_p, c.c_int, c.POINTER(c.c_int16)
    signatures = {
        "Get_Encoder_Size": [c.POINTER(c.c_int32)],
        "Get_Decoder_Size": [c.POINTER(c.c_int32)],
        "InitEncoder": [void, c.POINTER(Encoder)],
        "InitDecoder": [void],
        "Encode": [void, c.POINTER(Encoder), void, integer, void, short],
        "Decode": [void, c.POINTER(Decoder), integer, void, integer, void, short],
    }
    functions = {}
    for name, types in signatures.items():
        fn = getattr(lib, "SKP_Silk_SDK_" + name)
        fn.argtypes, fn.restype = types, integer
        functions[name] = fn

    def call(name, *args):
        if functions[name](*args) != 0:
            raise ValueError("SILK conversion failed")

    size = c.c_int32()
    call("Get_Encoder_Size" if action == "encode" else "Get_Decoder_Size", c.byref(size))
    state = c.create_string_buffer(size.value)
    output = bytearray()
    if action == "encode":
        control = Encoder()
        call("InitEncoder", state, c.byref(control))
        control = Encoder(rate, 24000, rate // 50, 25000, 0, 2, 0, 0)
        call_size = rate // 50 * 2
        if len(data) % 2 or not data:
            raise ValueError("Invalid PCM")
        output.extend(b"\x02#!SILK_V3")
        for offset in range(0, len(data), call_size):
            pcm = c.create_string_buffer(data[offset : offset + call_size].ljust(call_size, b"\0"))
            packet, length = c.create_string_buffer(1250), c.c_int16(1250)
            call("Encode", state, c.byref(control), pcm, rate // 50, packet, c.byref(length))
            if length.value <= 0:
                raise ValueError("Empty SILK packet")
            output.extend(struct.pack("<h", length.value) + packet.raw[: length.value])
    else:
        call("InitDecoder", state)
        control = Decoder(rate, 0, 0, 0, 0)
        offset = 10 if data.startswith(b"\x02#!SILK_V3") else 9 if data.startswith(b"#!SILK_V3") else -1
        if offset < 0 or len(data) > 1048576:
            raise ValueError("Invalid SILK")
        while offset < len(data):
            length = struct.unpack_from("<h", data, offset)[0]
            offset += 2
            if length == -1 and offset == len(data):
                break
            if not 0 < length <= 1250 or offset + length > len(data):
                raise ValueError("Invalid SILK packet")
            packet = c.create_string_buffer(data[offset : offset + length])
            offset += length
            for frame in range(5):
                pcm, samples = (c.c_int16 * 960)(), c.c_int16(960)
                call("Decode", state, c.byref(control), 0, packet, length, pcm, c.byref(samples))
                if not 0 <= samples.value <= 960:
                    raise ValueError("Invalid SILK output")
                output.extend(bytes(pcm)[: samples.value * 2])
                if len(output) > rate * 2 * 61:
                    raise ValueError("Audio too long")
                if not control.moreFrames:
                    break
            else:
                raise ValueError("Too many SILK frames")
    return bytes(output)


if __name__ == "__main__":
    action, source, destination, rate = sys.argv[1:]
    Path(destination).write_bytes(convert(action, Path(source).read_bytes(), int(rate)))
