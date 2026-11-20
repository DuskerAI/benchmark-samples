from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]


def rec(rtype, flags, cid, nsid, path, status, lba, nlb, seq, payload=b""):
    header = b"NCAP" + struct.pack("<BBHHBBQIQI", rtype, flags, cid, nsid, path, status, lba, nlb, seq, len(payload))
    pad = (4 - (len(payload) % 4)) % 4
    return header + payload + (b"\0" * pad)


def main():
    out = ROOT / "cases" / "rack-a" / "stream.ncap"
    rows = []
    rows.append(rec(5, 1, 1, 0, 0, 0, 0, 0, 1))
    rows.append(rec(5, 1, 1, 0, 1, 0, 0, 0, 2))
    rows.append(rec(1, 0, 10, 1, 0, 0, 0, 2, 10, b"AAAABBBB"))
    rows.append(rec(2, 0, 11, 1, 0, 0, 0, 1, 11, b"CCCC"))
    rows.append(rec(5, 0, 1, 0, 0, 0, 0, 0, 12))
    rows.append(rec(1, 0, 12, 1, 0, 0, 3, 1, 13, b"DDDD"))
    rows.append(rec(1, 0, 10, 1, 1, 0, 0, 2, 10, b"AAAABBBB"))
    rows.append(rec(4, 0, 40, 0, 1, 0, 0, 0, 14))
    rows.append(rec(1, 0, 20, 2, 1, 0, 0, 2, 20, b"EEEEFFFF"))
    rows.append(rec(2, 0, 21, 2, 1, 0, 5, 1, 21, b"GGGG"))
    rows.append(rec(3, 0, 22, 2, 1, 0, 0, 0, 22))
    rows.append(rec(1, 0, 23, 2, 1, 0, 5, 1, 23, b"HHHH"))
    rows.append(rec(4, 0, 41, 0, 1, 0, 0, 0, 24))
    rows.append(rec(1, 0, 30, 1, 1, 0, 6, 1, 25, b"IIII"))
    rows.append(rec(1, 0, 31, 1, 1, 0, 12, 1, 26, b"JJJJ"))
    rows.append(rec(1, 0, 32, 1, 1, 0, 13, 1, 27, b"KKKK"))
    rows.append(rec(6, 0, 99, 0, 1, 0, 0, 0, 28))
    rows.append(rec(1, 0, 88, 1, 1, 0, 2, 1, 29, b"LLLL"))
    data = b"".join(rows) + b"NCAP\x01\x00\x44"
    out.write_bytes(data)


if __name__ == "__main__":
    main()
