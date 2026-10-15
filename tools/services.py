"""Recover service marker candidates by stable RTTI names, not generic getter prologues."""

import json
import re
import struct
import sys
from pathlib import Path

from capstone import CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN, Cs


class Mach:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        self.segments = []
        cursor = 32
        for _ in range(struct.unpack_from("<I", self.data, 16)[0]):
            cmd, n = struct.unpack_from("<II", self.data, cursor)
            if cmd == 0x19:
                name, vm, vs, off, fs = struct.unpack_from("<16sQQQQ", self.data, cursor + 8)
                self.segments.append((vm, vs, off, fs, name.split(b"\0")[0]))
            cursor += n

    def read(self, addr, n=64):
        for vm, vs, off, fs, _ in self.segments:
            if vm <= addr and addr + n <= vm + fs:
                return self.data[addr - vm + off : addr - vm + off + n]
        return b""

    def ptr(self, addr):
        data = self.read(addr, 8)
        return struct.unpack("<Q", data)[0] & 0xFFFFFFFFF if data else 0

    def addr(self, offset):
        for vm, vs, off, fs, _ in self.segments:
            if off <= offset < off + fs:
                return offset - off + vm
        return None

    def rtti(self, code):
        engine = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        engine.detail = True
        registers = {}
        target = None
        for i in engine.disasm(self.read(code, 20), code):
            if i.mnemonic == "adrp":
                registers[i.operands[0].reg] = i.operands[1].imm
            if i.mnemonic == "add" and len(i.operands) == 3 and i.operands[1].reg in registers:
                target = registers[i.operands[1].reg] + i.operands[2].imm
        if target:
            name = self.ptr(target + 8)
            return self.read(name, 64).split(b"\0")[0].decode(errors="replace")
        return None

    def immediate_pair(self, address):
        a, b = struct.unpack("<II", self.read(address, 8))
        if a & 0x9F000000 != 0x90000000 or b & 0xFFC00000 != 0x91000000:
            return None
        imm = ((a >> 5) & 0x7FFFF) * 4 + ((a >> 29) & 3)
        if imm & 0x100000:
            imm -= 0x200000
        return (address & ~0xFFF) + (imm << 12) + ((b >> 10) & 0xFFF)


def main():
    old, new = Mach(sys.argv[1]), Mach(sys.argv[2])
    profile = json.loads(Path(sys.argv[3]).read_text())
    wanted = {}
    for name in ["contactService", "messageService", "ocrService"]:
        off = int(profile[name]["offset"], 16)
        target = old.immediate_pair(off + 24)
        if target:
            wanted[old.rtti(old.ptr(target + 16))] = name
    for name in [
        "sessionServiceMarkerOffset",
        "voiceFrontDescriptorOffset",
        "messageServiceDescriptorOffset",
    ]:
        target = int(profile[name], 16)
        wanted[old.rtti(old.ptr(target + 16))] = name
    print("wanted", wanted)
    resolved = {}
    # Getter prefix is shared across services; match the marker RTTI before accepting a candidate.
    prefix = old.read(int(profile["messageService"]["offset"], 16), 24)
    for m in re.finditer(re.escape(prefix), new.data):
        addr = new.addr(m.start())
        if addr is None or addr % 4:
            continue
        marker = new.immediate_pair(addr + 24)
        if not marker:
            continue
        name = new.rtti(new.ptr(marker + 16))
        if name in wanted:
            key = wanted[name]
            print(key, "getter", hex(addr), "marker", hex(marker), name)
            resolved.setdefault(key, []).append({"getter": hex(addr), "marker": hex(marker)})
    for match in re.finditer(re.escape(bytes.fromhex("080540f900f94092c0035fd6")), new.data):
        code = new.addr(match.start() - 8)
        if code is None or code % 4:
            continue
        name = new.rtti(code)
        if name not in wanted:
            continue
        for reference in re.finditer(re.escape(struct.pack("<I", code)), new.data):
            addr = new.addr(reference.start())
            if addr is None or addr % 8 or new.ptr(addr) != code:
                continue
            marker = addr - 16
            if new.read(new.ptr(marker), 4) != bytes.fromhex("c0035fd6"):
                continue
            key = wanted[name]
            print(key, "marker", hex(marker), name)
            resolved.setdefault(key, []).append({"marker": hex(marker)})
    Path(sys.argv[4]).write_text(json.dumps(resolved, indent=2))


if __name__ == "__main__":
    main()
