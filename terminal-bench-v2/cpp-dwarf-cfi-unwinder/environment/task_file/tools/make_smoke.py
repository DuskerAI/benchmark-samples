#!/usr/bin/env python3
import pathlib
import struct
import sys


def uleb(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        out.append(byte)
        if not value:
            return bytes(out)


def sleb(value):
    out = bytearray()
    more = True
    while more:
        byte = value & 0x7F
        value >>= 7
        sign = byte & 0x40
        more = not ((value == 0 and not sign) or (value == -1 and sign))
        if more:
            byte |= 0x80
        out.append(byte)
    return bytes(out)


out_dir = pathlib.Path(sys.argv[1])
out_dir.mkdir(parents=True, exist_ok=True)

cie = bytearray()
cie += struct.pack("<I", 1)
cie += uleb(1)
cie += sleb(-8)
cie += uleb(16)
cie += uleb(0)
cie_ins = bytes([0x0C]) + uleb(7) + uleb(0) + bytes([0x90]) + uleb(0)
cie += struct.pack("<H", len(cie_ins)) + cie_ins

fde = bytearray()
fde += struct.pack("<I", 1)
fde += bytes([0x00])
fde += struct.pack("<Q", 0x401000)
fde += struct.pack("<Q", 0x80)
fde += struct.pack("<H", 0)

(out_dir / "smoke.tcfi").write_bytes(b"TCFI" + bytes([1]) + struct.pack("<HH", 1, 1) + cie + fde)
(out_dir / "smoke.crash").write_text(
    "CFI " + str(out_dir / "smoke.tcfi") + "\n"
    "BASE 0x400000\n"
    "THREAD main\n"
    "START 0x401020\n"
    "REG 7 0x7000\n"
    "MEM 0x7000 0x0\n"
    "END\n",
    encoding="utf-8",
)
