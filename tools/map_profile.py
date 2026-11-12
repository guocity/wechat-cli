"""Developer aid: emit relocation candidates, never mark an untested profile as supported."""

import json
import re
import struct
import sys
from pathlib import Path


def pattern(data):
    parts = []
    for (word,) in struct.iter_unpack("<I", data):
        relative = (
            word & 0x1F000000 == 0x10000000
            or word & 0x7C000000 == 0x14000000
            or word & 0xFF000010 == 0x54000000
            or word & 0x7E000000 in {0x34000000, 0x36000000}
            or word & 0x3B000000 == 0x18000000
        )
        parts.append(b".{4}" if relative else re.escape(struct.pack("<I", word)))
    return b"".join(parts)


def candidates(data, target):
    # Search for the longest run of fixed instructions before checking the
    # complete pattern. Scanning the entire image with the regex is much slower
    # when a function starts with an address-dependent instruction.
    fixed = []
    for offset in range(0, len(data), 4):
        word = data[offset : offset + 4]
        if pattern(word) == b".{4}":
            continue
        if fixed and fixed[-1][1] == offset:
            fixed[-1] = (fixed[-1][0], offset + 4)
        else:
            fixed.append((offset, offset + 4))
    if not fixed:
        return []
    start, end = max(fixed, key=lambda run: run[1] - run[0])
    anchor = data[start:end]
    matcher = re.compile(pattern(data), re.DOTALL)
    matches = []
    position = target.find(anchor, start)
    while position >= 0:
        offset = position - start
        if offset % 4 == 0 and matcher.match(target, offset):
            matches.append(offset)
            if len(matches) == 2:
                break
        position = target.find(anchor, position + 1)
    return matches


def main():
    profile_path, old_path, new_path, output = map(Path, sys.argv[1:])
    profile = json.loads(profile_path.read_text())
    old, new = old_path.read_bytes(), new_path.read_bytes()
    result = {"functions": {}, "unresolved": []}
    for name, entry in profile.items():
        if not isinstance(entry, dict) or "signature" not in entry:
            continue
        offset = int(entry["offset"], 16)
        signature = bytes.fromhex(entry["signature"])
        if old[offset : offset + len(signature)] != signature:
            raise SystemExit(
                f"Source bytes for {name} do not match the profile. "
                "Use the matching build and extract its arm64 slice with lipo -thin arm64."
            )
        for size in [256, 192, 128, 96, 64, 48, 32]:
            matches = candidates(old[offset : offset + size], new)
            if len(matches) == 1:
                target = matches[0]
                result["functions"][name] = {
                    "offset": hex(target),
                    "signature": new[target : target + 32].hex(),
                    "matchedBytes": size,
                }
                print(name, hex(target), size, flush=True)
                break
        else:
            result["unresolved"].append(name)
    Path(output).write_text(json.dumps(result, indent=2))
    print("unresolved", result["unresolved"])


if __name__ == "__main__":
    main()
