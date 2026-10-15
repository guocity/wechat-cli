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


def main():
    profile_path, old_path, new_path, output = map(Path, sys.argv[1:])
    profile = json.loads(profile_path.read_text())
    old, new = old_path.read_bytes(), new_path.read_bytes()
    result = {"functions": {}, "unresolved": []}
    for name, entry in profile.items():
        if not isinstance(entry, dict) or "signature" not in entry:
            continue
        offset = int(entry["offset"], 16)
        for size in [256, 192, 128, 96, 64, 48, 32]:
            matches = [
                m.start()
                for m in re.finditer(pattern(old[offset : offset + size]), new, flags=re.DOTALL)
                if m.start() % 4 == 0
            ]
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
