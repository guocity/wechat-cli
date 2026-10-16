"""Account-scoped cache reads using native hardlink records, never database keys."""

import base64
import hashlib
import os
import re
import struct
from pathlib import Path

from wechat_cli.core.errors import AppError
from wechat_cli.core.media import MAX_MEDIA, decode_media, mime_type


class MediaCache:
    def __init__(self, backend):
        self.backend = backend

    def context(self):
        value = self.backend._request("mediaContext")
        root = Path(value["root"]).resolve(strict=True)
        account = value["account"]
        if not root.is_absolute() or not (root.name == account or root.name.startswith(account + "_")):
            raise AppError("native_account_not_ready", "Cannot identify the active media cache", 409)
        return root, account

    def matching_paths(self, root, names):
        names = set(names)
        if any(
            not name or name in {".", ".."} or "/" in name or "\\" in name or ":" in name for name in names
        ):
            raise AppError("native_invalid_media_id", "Invalid cache file name", 422)
        examined = 0
        for area in [root / "msg", root / "cache", root / "resource"]:
            if not area.is_dir() or not area.resolve().is_relative_to(root):
                continue
            for directory, folders, files in os.walk(area, followlinks=False):
                # Junctions and symlinks must not escape this account's cache.
                folders[:] = [
                    name
                    for name in folders
                    if (Path(directory) / name).resolve().is_relative_to(root)
                    and not (Path(directory) / name).is_symlink()
                ]
                examined += len(files) + len(folders)
                if examined > 100000:
                    raise AppError("native_result_too_large", "Media cache scan exceeds its bound", 413)
                for name in names.intersection(files):
                    path = (Path(directory) / name).resolve(strict=True)
                    if path.is_relative_to(root) and path.is_file():
                        yield path

    def decode(self, data):
        if data[:6] in {b"\x07\x08V1\x08\x07", b"\x07\x08V2\x08\x07"}:
            if len(data) < 31:
                raise AppError("native_media_invalid", "Truncated image cache", 422)
            plain, tail = struct.unpack_from("<II", data, 6)
            encrypted = (plain // 16 + 1) * 16
            if not 0 < plain <= 1024 * 1024 or tail > MAX_MEDIA or 15 + encrypted + tail > len(data):
                raise AppError("native_media_invalid", "Invalid image cache lengths", 422)
            result = self.backend._request(
                "decodeImageHeader",
                {
                    "version": data[3] - 48,
                    "bytes": list(data[15 : 15 + encrypted]),
                },
            )
            prefix = bytes(result["bytes"])
            xor = result["xor"]
            if len(prefix) != plain or type(xor) is not int or not 0 <= xor <= 255:
                raise AppError("native_media_invalid", "Invalid native image header", 422)
            middle_end = len(data) - tail
            data = prefix + data[15 + encrypted : middle_end] + bytes(b ^ xor for b in data[middle_end:])
        else:
            data = decode_media(data)
        if data and data.startswith((b"wxgf", b"wxpc", b"hevc")):
            data = self.backend.native_tool("render", data)
        return data

    def cached(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", value):
            raise AppError("native_invalid_media_id", "Expected a media digest", 422)
        root, account = self.context()
        rows = []
        for table in ["image_hardlink_info_v4", "video_hardlink_info_v4"]:
            rows.extend(
                self.backend._query(
                    "hardlink.db",
                    f"SELECT file_name,type FROM {table} WHERE md5=? LIMIT 16",
                    [value.lower()],
                    16,
                )
            )
        names = {row["file_name"] for row in rows}
        names.update([value, value + ".dat", value + "_t.dat", value + ".mp4", value + ".jpg"])
        result, total, output_size = [], 0, 0
        for path in self.matching_paths(root, names):
            with path.open("rb") as source:
                data = source.read(MAX_MEDIA + 1)
            total += len(data)
            if len(data) > MAX_MEDIA or total > MAX_MEDIA or len(result) >= 16:
                raise AppError("media_too_large", "Cached media exceeds 32 MiB", 413)
            decoded = self.decode(data)
            if decoded and mime_type(decoded):
                output_size += len(decoded)
                if output_size > MAX_MEDIA:
                    raise AppError("media_too_large", "Decoded cache result exceeds 32 MiB", 413)
                result.append(
                    {
                        "dataBase64": base64.b64encode(decoded).decode(),
                        "isThumbnail": "_t." in path.name or "thumb" in path.name.lower(),
                    }
                )
        if self.backend.self_id() != account:
            raise AppError("native_account_changed", "Active WeChat account changed", 409)
        return {"files": result}

    def attachment(self, message):
        details = message["details"]
        digest, size = details.get("md5"), details.get("sizeBytes", 0)
        if not digest or not re.fullmatch(r"[a-fA-F0-9]{32}", digest) or not 0 < size <= 128 * 1024**2:
            raise AppError("native_media_invalid", "Attachment has no verified digest and size", 422)
        rows = self.backend._query(
            "hardlink.db", "SELECT file_name FROM file_hardlink_info_v4 WHERE md5=? LIMIT 16", [digest], 16
        )
        root, account = self.context()
        found = {}
        for path in self.matching_paths(root, {r["file_name"] for r in rows}):
            if path.stat().st_size != size:
                continue
            with path.open("rb") as source:
                data = source.read(size + 1)
            if len(data) == size and hashlib.md5(data).hexdigest() == digest:
                # Identical hardlinks/copies are a single attachment, not ambiguity.
                found[digest] = {"dataBase64": base64.b64encode(data).decode()}
        if self.backend.self_id() != account:
            raise AppError("native_account_changed", "Active WeChat account changed", 409)
        return {"files": list(found.values())}
