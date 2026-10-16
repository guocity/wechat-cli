"""Bounded PNG/JPEG dimensions without platform-specific Python binary wheels."""

import struct
import zlib

from wechat_cli.core.errors import AppError


def dimensions(data):
    if not 1 <= len(data) <= 8 * 1024**2:
        raise AppError("image_too_large", "OCR images must be 1 byte through 8 MiB", 413)
    width = height = None
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            cursor, first, ended, pixels = 8, True, False, False
            while cursor < len(data):
                length = struct.unpack_from(">I", data, cursor)[0]
                kind, value = data[cursor + 4 : cursor + 8], data[cursor + 8 : cursor + 8 + length]
                if cursor + length + 12 > len(data) or len(kind) != 4:
                    raise ValueError()
                crc = struct.unpack_from(">I", data, cursor + 8 + length)[0]
                if zlib.crc32(kind + value) & 0xFFFFFFFF != crc:
                    raise ValueError()
                if first:
                    if kind != b"IHDR" or length != 13:
                        raise ValueError()
                    width, height = struct.unpack_from(">II", value)
                    first = False
                elif kind == b"IHDR":
                    raise ValueError()
                if kind == b"IDAT":
                    pixels = True
                cursor += length + 12
                if kind == b"IEND":
                    if length or cursor != len(data):
                        raise ValueError()
                    ended = True
                    break
            if not ended or not pixels:
                raise ValueError()
        elif data.startswith(b"\xff\xd8"):
            cursor, scan = 2, False
            while cursor < len(data) - 2:
                if data[cursor] != 255:
                    raise ValueError()
                while cursor < len(data) and data[cursor] == 255:
                    cursor += 1
                marker = data[cursor]
                cursor += 1
                if marker in {0, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                    raise ValueError()
                length = struct.unpack_from(">H", data, cursor)[0]
                if length < 2 or cursor + length > len(data):
                    raise ValueError()
                if marker in {0xC0, 0xC1, 0xC2}:
                    if length < 8:
                        raise ValueError()
                    height, width = struct.unpack_from(">HH", data, cursor + 3)
                if marker == 0xDA:
                    if data.find(b"\xff\xd9", cursor + length) < 0:
                        raise ValueError()
                    scan = True
                    break
                cursor += length
            if not scan:
                raise ValueError()
        if not width or not height:
            raise ValueError()
    except (ValueError, IndexError, struct.error):
        raise AppError("invalid_image_format", "OCR supports valid PNG and JPEG images", 422) from None
    if width > 16384 or height > 16384 or width * height > 16000000:
        raise AppError("image_dimensions_unsupported", "OCR images must be at most 16 megapixels", 413)
    return {"width": width, "height": height}
