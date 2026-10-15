"""Rank static function candidates for manual REA validation; not a runtime resolver."""

import difflib
import json
import struct
import sys
from pathlib import Path


def normalized(data):
    result = []
    for (w,) in struct.iter_unpack("<I", data):
        if w & 0x1F000000 == 0x10000000:
            w &= 0x9F00001F
        elif w & 0x7C000000 == 0x14000000:
            w &= 0xFC000000
        elif w & 0xFF000010 == 0x54000000:
            w &= 0xFF00001F
        elif w & 0x7E000000 == 0x34000000:
            w &= 0xFF00001F
        elif w & 0x7E000000 == 0x36000000:
            w &= 0xFFF8001F
        elif w & 0x3B000000 == 0x18000000:
            w &= 0xFF00001F
        result.append(w)
    return result


def main():
    profile_path, old_path, new_path, mapped_path = map(Path, sys.argv[1:])
    p = json.loads(profile_path.read_text())
    old = old_path.read_bytes()
    new = new_path.read_bytes()
    mapped = json.loads(mapped_path.read_text())
    established = [
        (int(p[k]["offset"], 16), int(v["offset"], 16))
        for k, v in mapped["functions"].items()
        if v["matchedBytes"] >= 96 and -0x100000 < int(v["offset"], 16) - int(p[k]["offset"], 16) < 0x500000
    ]
    for key in mapped["unresolved"]:
        off = int(p[key]["offset"], 16)
        anchor = min(established, key=lambda pair: abs(pair[0] - off))
        expected = off + anchor[1] - anchor[0]
        signature = bytes.fromhex(p[key]["signature"])
        candidates = []
        for length in [min(len(signature), 16), 12, 8]:
            start = max(0, expected - 0x180000)
            end = min(len(new), expected + 0x180000)
            while (start := new.find(signature[:length], start, end)) >= 0:
                if start % 4 == 0:
                    candidates.append(start)
                start += 1
            if candidates:
                break
        scores = []
        for target in candidates:
            score = difflib.SequenceMatcher(
                None, normalized(old[off : off + 512]), normalized(new[target : target + 512]), autojunk=False
            ).ratio()
            scores.append((round(score, 4), hex(target), round(abs(target - expected) / 0x10000, 2)))
        scores.sort(reverse=True)
        print(key, scores[:4], flush=True)
        mapped.setdefault("candidates", {})[key] = scores[:4]
    mapped_path.write_text(json.dumps(mapped, indent=2))


if __name__ == "__main__":
    main()
