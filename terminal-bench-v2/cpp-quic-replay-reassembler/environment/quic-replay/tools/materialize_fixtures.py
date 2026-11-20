from pathlib import Path
import json

ROOT = Path("/opt/quic-replay/fixtures")


def vint(n):
    if n < 2**6:
        return bytes([n])
    if n < 2**14:
        return bytes([0x40 | (n >> 8), n & 0xFF])
    if n < 2**30:
        return bytes([0x80 | ((n >> 24) & 0x3F), (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])
    return bytes([0xC0 | ((n >> 56) & 0x3F), (n >> 48) & 0xFF, (n >> 40) & 0xFF, (n >> 32) & 0xFF, (n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])


def stream_frame(stream_id, offset, data, fin=False, omit_len=False):
    t = 0x08
    if offset:
        t |= 0x04
    if not omit_len:
        t |= 0x02
    if fin:
        t |= 0x01
    out = vint(t) + vint(stream_id)
    if offset:
        out += vint(offset)
    if not omit_len:
        out += vint(len(data))
    return out + data


def packet(conn, direction, epoch, key_phase, pn, pn_len, frames):
    d = 0 if direction == "client" else 1
    truncated = pn & ((1 << (8 * pn_len)) - 1)
    body = bytes([len(conn)]) + conn.encode() + bytes([d, epoch, key_phase, pn_len]) + truncated.to_bytes(pn_len, "big") + b"".join(frames)
    return b"QR" + vint(len(body)) + body


def write_case(name, packets, events):
    (ROOT / "captures").mkdir(parents=True, exist_ok=True)
    (ROOT / "qlogs").mkdir(parents=True, exist_ok=True)
    (ROOT / "captures" / f"{name}.qcap").write_bytes(b"QRC1" + b"".join(packets))
    with (ROOT / "qlogs" / f"{name}.jsonl").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")


def event(conn, direction, epoch, key_phase, pn, kind="received"):
    return {"connection": conn, "direction": direction, "epoch": epoch, "key_phase": key_phase, "packet_number": pn, "event": kind}


write_case(
    "alpha",
    [
        packet("cid-a", "client", 1, 0, 1, 1, [stream_frame(0, 0, b"hello ")]),
        packet("cid-a", "server", 1, 0, 1, 1, [stream_frame(1, 0, b"ack", True)]),
        packet("cid-a", "client", 1, 0, 2, 1, [stream_frame(0, 6, b"alpha", True)]),
    ],
    [event("cid-a", "client", 1, 0, 1), event("cid-a", "server", 1, 0, 1), event("cid-a", "client", 1, 0, 2)],
)

write_case(
    "bravo",
    [
        packet("cid-b", "client", 1, 0, 254, 1, [stream_frame(0, 0, b"open")]),
        packet("cid-b", "client", 1, 0, 255, 1, [stream_frame(0, 4, b"-mid")]),
        packet("cid-b", "client", 1, 0, 256, 1, [stream_frame(0, 70, b"end", True), b"\x00\x00"]),
        packet("cid-b", "server", 1, 0, 7, 1, [stream_frame(3, 0, b"b", True, omit_len=True)]),
    ],
    [event("cid-b", "client", 1, 0, 254), event("cid-b", "client", 1, 0, 255), event("cid-b", "client", 1, 0, 256), event("cid-b", "server", 1, 0, 7)],
)

write_case(
    "charlie",
    [
        packet("cid-c", "client", 1, 0, 10, 1, [stream_frame(0, 0, b"abc")]),
        packet("cid-c", "client", 1, 0, 10, 1, [stream_frame(0, 0, b"abc")]),
        packet("cid-c", "client", 1, 0, 11, 1, [stream_frame(0, 3, b"xxx")]),
        packet("cid-c", "client", 1, 0, 12, 1, [stream_frame(0, 3, b"def", True)]),
        packet("cid-c", "server", 1, 0, 12, 1, [stream_frame(0, 0, b"srv", True)]),
        packet("cid-c", "client", 1, 1, 513, 1, [stream_frame(2, 0, b"phase", True)]),
    ],
    [
        event("cid-c", "client", 1, 0, 10),
        event("cid-c", "client", 1, 0, 11, "lost"),
        event("cid-c", "client", 1, 0, 12),
        event("cid-c", "server", 1, 0, 12),
        event("cid-c", "client", 1, 1, 513),
    ],
)
