"""Map an ambiguous callee through a uniquely matched surrounding call site."""

import json
import re
import struct
import sys
from pathlib import Path

from map_profile import pattern


def main():
    old_path, new_path, address = sys.argv[1:]
    target = int(address, 0)
    old = Path(old_path).read_bytes()
    new = Path(new_path).read_bytes()
    found = []
    for index, (word,) in enumerate(struct.iter_unpack("<I", old[: len(old) // 4 * 4])):
        if word & 0xFC000000 != 0x94000000:
            continue
        relative = word & 0x3FFFFFF
        if relative & 0x2000000:
            relative -= 0x4000000
        pc = index * 4
        if pc + relative * 4 != target:
            continue
        for before, after in [(128, 128), (96, 96), (64, 64), (32, 32), (16, 16)]:
            start = max(0, pc - before)
            end = pc + after + 4
            matches = [
                m.start() for m in re.finditer(pattern(old[start:end]), new, re.DOTALL) if m.start() % 4 == 0
            ]
            if len(matches) == 1:
                newpc = matches[0] + pc - start
                word = struct.unpack_from("<I", new, newpc)[0]
                if word & 0xFC000000 != 0x94000000:
                    continue
                relative = word & 0x3FFFFFF
                if relative & 0x2000000:
                    relative -= 0x4000000
                value = {
                    "oldCaller": hex(pc),
                    "newCaller": hex(newpc),
                    "target": hex(newpc + relative * 4),
                    "matchedBytes": end - start,
                }
                print(value, flush=True)
                found.append(value)
                break
        if len(found) >= 5:
            break
    print("targets", sorted({x["target"] for x in found}))


if __name__ == "__main__":
    main()
