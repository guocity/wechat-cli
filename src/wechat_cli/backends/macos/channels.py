"""Recover message-correlated MP4 bytes from an approved local playback renderer.

Uses read-only Gadget RPC. No native code patching, proxy, arbitrary URL input,
or uncorrelated cache-file selection. The optional native opener loads the selected card first; signed playback
URLs and media buffers are transient.
"""

import json
import re
import socket
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from wechat_cli.core.errors import AppError
from wechat_cli.core.media import cdn_url, complete_mp4, download_media

from .processes import process_snapshot

MAX_SCAN = 1024 * 1024**2
CHUNK = 4 * 1024**2
HEADER = 128 * 1024
RENDERER = Path(
    "Contents/MacOS/WeChatAppEx.app/Contents/Frameworks/"
    "WeChatAppEx Framework.framework/Versions/C/Helpers/"
    "WeChatAppEx Helper (Renderer).app/Contents/MacOS/WeChatAppEx Helper (Renderer)"
)
SOURCE = """rpc.exports={
 identity(){return {pid:Process.id,path:Process.mainModule.path,policy:Process.codeSigningPolicy};},
 ranges(){return Process.enumerateRanges({protection:'rw-',coalesce:true})
   .filter(r=>!r.file).map(r=>({base:r.base.toString(),size:r.size}));},
 read(base,size){return ptr(base).readVolatile(size);}
};"""


def playback_urls(text, original):
    """Require the exact encrypted-file key, path, and any digest supplied by the card."""
    expected = urlsplit(cdn_url(original))
    keys = parse_qs(expected.query)
    if not keys.get("encfilekey"):
        return []
    candidates = []
    for match in re.finditer(r'"url"\s*:\s*("(?:[^"\\]|\\.)*")', text):
        try:
            candidates.append(json.loads(match[1]))
        except (ValueError, TypeError):
            continue
    # Chromium also retains the URL as a standalone string, without a JSON field.
    candidates.extend(re.findall(r"https://[A-Za-z0-9%_~:/?=&.+;@!$()*,-]+", text))
    result = []
    for candidate in candidates:
        try:
            parsed = urlsplit(cdn_url(candidate))
            query = parse_qs(parsed.query)
            if (
                parsed.path == expected.path
                and query.get("encfilekey") == keys["encfilekey"]
                and (not keys.get("m") or query.get("m") == keys["m"])
                and query.get("token")
                and candidate not in result
            ):
                result.append(candidate)
        except (ValueError, TypeError, AppError):
            continue
    return result


def clear_header(reader, address, encrypted):
    """A decrypted prefix must match this exact download beyond its cipher region."""
    if len(encrypted) < HEADER + 64:
        return None
    try:
        block = reader.read(hex(address), HEADER + 64)
    except Exception:
        return None
    if not block or block[HEADER : HEADER + 64] != encrypted[HEADER : HEADER + 64]:
        return None
    candidate = bytes(block[:HEADER]) + encrypted[HEADER:]
    return candidate if complete_mp4(candidate) else None


def recover_reader(reader, original, *, deadline, downloader=download_media):
    keys = parse_qs(urlsplit(original).query)
    needle = keys.get("encfilekey", [""])[0]
    if not needle:
        return None
    needles = {needle, quote(needle, safe="")}
    patterns = [(word.encode(encoding), encoding) for word in needles for encoding in ("latin1", "utf-16le")]
    encrypted = name = None
    addresses, tried_urls, checked = [], set(), set()
    scanned = 0
    for region in reader.ranges():
        base, size = int(region["base"], 16), min(region["size"], 256 * 1024**2)
        for offset in range(0, size, CHUNK):
            length = min(CHUNK + HEADER + 64, size - offset)
            if time.monotonic() > deadline or scanned + length > MAX_SCAN:
                return None
            scanned += length
            try:
                data = reader.read(hex(base + offset), length)
            except Exception:
                continue
            if not data:
                continue
            for match in re.finditer(b"ftyp", data):
                start = match.start() - 4
                if start >= 0 and data[start + 8 : start + 12] in {
                    b"isom",
                    b"iso5",
                    b"iso6",
                    b"mp42",
                    b"mp41",
                    b"avc1",
                }:
                    address = base + offset + start
                    if address not in addresses and len(addresses) < 128:
                        addresses.append(address)
            if encrypted is None:
                candidates = []
                for pattern, encoding in patterns:
                    offset_in_chunk = 0
                    for _ in range(128):
                        at = data.find(pattern, offset_in_chunk)
                        if at < 0:
                            break
                        offset_in_chunk = at + len(pattern)
                        lo, hi = max(0, at - 8192), min(len(data), at + 131072)
                        if encoding == "utf-16le":
                            lo += lo % 2
                            hi -= hi % 2
                        text = data[lo:hi].decode(encoding, errors="replace")
                        candidates.extend(playback_urls(text, original))
                for url in candidates:
                    if url in tried_urls or len(tried_urls) >= 3:
                        continue
                    tried_urls.add(url)
                    try:
                        encrypted, name = downloader(url, maximum=128 * 1024**2, with_name=True)
                    except AppError:
                        continue
                    if complete_mp4(encrypted):
                        return encrypted, name
                    break
            if encrypted is not None:
                for address in addresses:
                    if address in checked:
                        continue
                    checked.add(address)
                    result = clear_header(reader, address, encrypted)
                    if result is not None:
                        return result, name

    return None


def recover(app, original, *, prepare=None):
    import frida

    expected = (Path(app) / RENDERER).resolve()
    config_path = expected.parent / "g.config"
    try:
        config = json.loads(config_path.read_text())
        interaction = config["interaction"]
        port = interaction["port"]
        token = interaction["token"]
        if (
            config.get("code_signing") != "required"
            or interaction.get("type") != "listen"
            or interaction.get("address") != "127.0.0.1"
            or type(port) is not int
            or not 1024 <= port <= 65504
            or not isinstance(token, str)
            or not re.fullmatch(r"[a-f0-9]{64}", token)
        ):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise AppError(
            "channels_research_runtime_required",
            "Channels recovery requires the approved WeChat playback research build.",
            424,
        ) from None

    def release(endpoint, session, script):
        # Navigation can destroy a renderer between any two RPCs. Cleanup must
        # not turn successful recovery (or a normal retry) into an HTTP 500.
        for cleanup in (
            lambda: script.unload() if script else None,
            lambda: session.detach() if session else None,
            lambda: frida.get_device_manager().remove_remote_device(endpoint),
        ):
            try:
                cleanup()
            except Exception:
                pass

    if prepare is not None:
        prepare()
        time.sleep(3)  # Allow the native player to create its renderer and load media.
    deadline = time.monotonic() + 75
    # AppEx can replace the initial renderer while navigating to the selected feed.
    # Rediscover endpoints and rescan after loading rather than pinning that first snapshot.
    while time.monotonic() < deadline:
        pids = set()
        for line in process_snapshot().splitlines():
            pair = line.strip().split(None, 1)
            if len(pair) == 2 and pair[0].isdigit() and Path(pair[1]).resolve() == expected:
                pids.add(int(pair[0]))
        manager = frida.get_device_manager()
        sessions = []
        try:
            for candidate in range(port, port + 16):
                try:
                    with socket.create_connection(("127.0.0.1", candidate), timeout=0.1):
                        pass
                except OSError:
                    continue
                endpoint = f"127.0.0.1:{candidate}"
                session = script = None
                try:
                    device = manager.add_remote_device(endpoint, token=token)
                    session = device.attach("Gadget")
                    script = session.create_script(SOURCE)
                    script.load()
                    identity = script.exports_sync.identity()
                    if (
                        identity["pid"] not in pids
                        or Path(identity["path"]).resolve() != expected
                        or identity["policy"] != "required"
                    ):
                        raise ValueError()
                    sessions.append((identity["pid"], endpoint, session, script))
                except Exception:
                    release(endpoint, session, script)
            for _, _, _, script in sorted(sessions, key=lambda item: item[0], reverse=True):
                if time.monotonic() > deadline:
                    break
                try:
                    result = recover_reader(script.exports_sync, original, deadline=deadline)
                except (frida.InvalidOperationError, frida.TransportError):
                    continue
                if result:
                    return result
        finally:
            for _, endpoint, session, script in sessions:
                release(endpoint, session, script)
        if time.monotonic() < deadline:
            time.sleep(1)
    raise AppError(
        "channels_playback_not_loaded",
        "WeChat opened the card but its playable media was not available before the timeout. Retry once the native player has loaded.",
        409,
    )
