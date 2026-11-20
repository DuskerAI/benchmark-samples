import csv
import hashlib
import json
import subprocess
from pathlib import Path


APP = Path("/opt/quic-replay")
BIN = APP / "bin" / "quic-replay"
DEFAULT_CONF = Path("/etc/quic-replay/runtime.conf")
PROTECTED_HASHES = {
    Path("/etc/quic-replay/runtime.conf"): "3d724930eb6af46bd6055cc96c30e4856732cd372dec346590885c1c4d5de424",
    APP / "fixtures" / "manifest.tsv": "e5e020f9727345ba89610591a5e9f41edeb549784308f2dc8a7571672b487a30",
    APP / "fixtures" / "captures" / "alpha.qcap": "c8ec1f4c7cc63478ae849a2727d0bbeecf3ebfa6a38375846c90e3e81daa8e13",
    APP / "fixtures" / "captures" / "bravo.qcap": "cb150c8828967e1d4d71ae338961684b5014b30544c35294ed04ef7d64b133f8",
    APP / "fixtures" / "captures" / "charlie.qcap": "1ef5fc5d4d88784c07c08a6e8357f2cf2ce793a3ce518c214a4e7e1ab1f0979b",
    APP / "fixtures" / "qlogs" / "alpha.jsonl": "81a73467798e32f00698fd49cfeaf3cdc1825b3c604d359ac224e6a2ebb3e797",
    APP / "fixtures" / "qlogs" / "bravo.jsonl": "07155f9969a9696ad420eef0f03c00acc214f57e34482af7a9b441f5c339bb26",
    APP / "fixtures" / "qlogs" / "charlie.jsonl": "07a73baf4067157a16f8d4848343fee41285d1878ae2d07664b45a0d6e04ef28",
}
DOC_CONTRACT_PHRASES = [
    "Packet numbers are reconstructed",
    "Matching includes connection, direction, epoch, key phase",
    "earliest qlog-file-order entry gives the trace timeline position",
    "Streams are reassembled independently by connection, direction, and stream id",
    "Duplicate reporting is part of replay acceptance",
    "packet_trace.csv",
    "replay_summary.json",
    "streams.jsonl",
    "gaps.csv",
]


def enc_vint(n: int) -> bytes:
    if n < 2**6:
        return bytes([n])
    if n < 2**14:
        return bytes([0x40 | (n >> 8), n & 0xFF])
    if n < 2**30:
        return bytes([0x80 | ((n >> 24) & 0x3F), (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])
    return bytes([0xC0 | ((n >> 56) & 0x3F), (n >> 48) & 0xFF, (n >> 40) & 0xFF, (n >> 32) & 0xFF, (n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])


def dec_vint(data: bytes, pos: int, end: int):
    first = data[pos]
    pos += 1
    length = 1 << (first >> 6)
    value = first & 0x3F
    for _ in range(1, length):
        value = (value << 8) | data[pos]
        pos += 1
    return value, pos


def make_frame(stream_id: int, offset: int, payload: bytes, fin=False, omit_len=False) -> bytes:
    typ = 0x08
    if offset:
        typ |= 0x04
    if not omit_len:
        typ |= 0x02
    if fin:
        typ |= 0x01
    out = enc_vint(typ) + enc_vint(stream_id)
    if offset:
        out += enc_vint(offset)
    if not omit_len:
        out += enc_vint(len(payload))
    return out + payload


def make_packet(conn, direction, epoch, key_phase, pn, pn_len, frames) -> bytes:
    truncated = pn & ((1 << (8 * pn_len)) - 1)
    body = bytes([len(conn)]) + conn.encode() + bytes([0 if direction == "client" else 1, epoch, key_phase, pn_len]) + truncated.to_bytes(pn_len, "big") + b"".join(frames)
    return b"QR" + enc_vint(len(body)) + body


def write_case(root: Path, name: str, packets, events):
    cap_path = root / "captures" / f"{name}.qcap"
    qlog_path = root / "qlogs" / f"{name}.jsonl"
    cap_path.parent.mkdir(parents=True, exist_ok=True)
    qlog_path.parent.mkdir(parents=True, exist_ok=True)
    cap_path.write_bytes(b"QRC1" + b"".join(packets))
    with qlog_path.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")


def ev(conn, direction, epoch, key_phase, pn, kind="received"):
    return {"connection": conn, "direction": direction, "epoch": epoch, "key_phase": key_phase, "packet_number": pn, "event": kind}


def parse_capture(path: Path):
    data = path.read_bytes()
    assert data[:4] == b"QRC1"
    pos = 4
    packets = []
    while pos < len(data):
        assert data[pos:pos + 2] == b"QR"
        pos += 2
        body_len, pos = dec_vint(data, pos, len(data))
        end = pos + body_len
        clen = data[pos]
        pos += 1
        conn = data[pos:pos + clen].decode()
        pos += clen
        direction = "client" if data[pos] == 0 else "server"
        pos += 1
        epoch, key_phase, pn_len = data[pos], data[pos + 1], data[pos + 2]
        pos += 3
        trunc = int.from_bytes(data[pos:pos + pn_len], "big")
        pos += pn_len
        frames = []
        while pos < end:
            typ, pos = dec_vint(data, pos, end)
            if typ == 0:
                continue
            assert typ & 0xF8 == 0x08
            sid, pos = dec_vint(data, pos, end)
            if typ & 0x04:
                off, pos = dec_vint(data, pos, end)
            else:
                off = 0
            if typ & 0x02:
                length, pos = dec_vint(data, pos, end)
            else:
                length = end - pos
            payload = data[pos:pos + length]
            pos += length
            frames.append({"stream_id": sid, "offset": off, "fin": bool(typ & 0x01), "data": payload})
        packets.append({"connection": conn, "direction": direction, "epoch": epoch, "key_phase": key_phase, "pn_len": pn_len, "truncated": trunc, "frames": frames})
        pos = end
    return packets


def reconstruct(truncated: int, pn_len: int, expected: int) -> int:
    window = 1 << (8 * pn_len)
    half = window // 2
    mask = window - 1
    candidate = (expected & ~mask) | truncated
    if candidate + half <= expected:
        candidate += window
    elif candidate > expected + half and candidate >= window:
        candidate -= window
    return candidate


def reference(capture_root: Path, qlog_root: Path):
    packets = []
    largest = {}
    for path in sorted(capture_root.rglob("*.qcap")):
        for packet in parse_capture(path):
            space = (packet["connection"], packet["direction"], packet["epoch"])
            expected = largest.get(space, -1) + 1
            packet["packet_number"] = reconstruct(packet["truncated"], packet["pn_len"], expected)
            largest[space] = max(largest.get(space, -1), packet["packet_number"])
            packets.append(packet)
    order = {}
    accepted = set()
    idx = 0
    for path in sorted(qlog_root.rglob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line:
                continue
            event = json.loads(line)
            key = (event["connection"], event["direction"], event["epoch"], event["key_phase"], event["packet_number"])
            order.setdefault(key, idx)
            idx += 1
            if event["event"] == "received":
                accepted.add(key)
    trace = []
    streams = {}
    seen = set()
    conns = set()
    replayed = duplicates = 0
    for packet in sorted(packets, key=lambda p: (order.get((p["connection"], p["direction"], p["epoch"], p["key_phase"], p["packet_number"]), 10**9), p["connection"], p["direction"], p["epoch"], p["key_phase"], p["packet_number"])):
        conns.add(packet["connection"])
        key = (packet["connection"], packet["direction"], packet["epoch"], packet["key_phase"], packet["packet_number"])
        bytes_in_packet = sum(len(f["data"]) for f in packet["frames"])
        if key not in accepted:
            decision = "skipped"
        elif key in seen:
            duplicates += 1
            decision = "duplicate"
        else:
            seen.add(key)
            replayed += 1
            decision = "replayed"
            for frame in packet["frames"]:
                skey = (packet["connection"], packet["direction"], frame["stream_id"])
                state = streams.setdefault(skey, {"segments": {}, "fin": None})
                old = state["segments"].get(frame["offset"])
                if old is None:
                    state["segments"][frame["offset"]] = frame["data"]
                else:
                    assert old == frame["data"]
                if frame["fin"]:
                    state["fin"] = frame["offset"] + len(frame["data"])
        trace.append([packet["connection"], packet["direction"], str(packet["epoch"]), str(packet["key_phase"]), str(packet["packet_number"]), decision, str(len(packet["frames"])), str(bytes_in_packet)])
    stream_rows = []
    gap_rows = []
    complete = incomplete = bytes_reassembled = 0
    for (conn, direction, sid), state in sorted(streams.items()):
        fin = state["fin"]
        if fin is None:
            incomplete += 1
            continue
        cur = 0
        payload = bytearray()
        gaps = []
        for off, data in sorted(state["segments"].items()):
            if off > cur:
                gaps.append((cur, off))
                cur = off
            if off + len(data) > cur:
                start = max(0, cur - off)
                payload.extend(data[start:])
                cur = off + len(data)
        if cur < fin:
            gaps.append((cur, fin))
        if gaps:
            incomplete += 1
            for start, end in gaps:
                gap_rows.append([conn, direction, str(sid), str(start), str(end), "missing"])
        else:
            complete += 1
            bytes_reassembled += len(payload)
            stream_rows.append({"connection": conn, "direction": direction, "stream_id": sid, "fin_offset": fin, "payload_hex": payload.hex()})
    summary = {"connections": len(conns), "packets_seen": len(packets), "packets_replayed": replayed, "duplicates": duplicates, "streams_complete": complete, "streams_incomplete": incomplete, "bytes_reassembled": bytes_reassembled}
    return summary, trace, stream_rows, gap_rows


def run_tool(conf: Path):
    subprocess.run([str(APP / "scripts" / "build.sh")], check=True)
    subprocess.run([str(BIN), str(conf)], check=True)


def read_conf(path: Path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if line and "=" in line)


def read_outputs(outdir: Path):
    summary = json.loads((outdir / "replay_summary.json").read_text())
    with (outdir / "packet_trace.csv").open(newline="") as f:
        trace = list(csv.reader(f))[1:]
    streams = [json.loads(line) for line in (outdir / "streams.jsonl").read_text().splitlines() if line]
    with (outdir / "gaps.csv").open(newline="") as f:
        gaps = list(csv.reader(f))[1:]
    return summary, trace, streams, gaps


def assert_exact_outputs(conf: Path):
    cfg = read_conf(conf)
    expected = reference(Path(cfg["capture_root"]), Path(cfg["qlog_root"]))
    actual = read_outputs(Path(cfg["output_dir"]))
    assert actual == expected
    assert sorted(p.name for p in Path(cfg["output_dir"]).iterdir()) == ["gaps.csv", "packet_trace.csv", "replay_summary.json", "streams.jsonl"]
    for path in Path(cfg["output_dir"]).iterdir():
        assert path.read_bytes().endswith(b"\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_protected_contract_inputs_unchanged():
    """Check the documented immutable docs, config, and bundled fixtures are intact."""
    for path, expected in PROTECTED_HASHES.items():
        assert path.is_file(), f"missing protected file: {path}"
        assert sha256(path) == expected, f"protected file changed: {path}"
    docs = (APP / "docs" / "format.md").read_text(encoding="utf-8")
    for phrase in DOC_CONTRACT_PHRASES:
        assert phrase in docs, f"contract docs changed or incomplete: {phrase}"


def test_bundled_replay_matches_documented_behavior():
    """Check bundled PN reconstruction, qlog filtering, reassembly, schemas, and newlines."""
    run_tool(DEFAULT_CONF)
    assert_exact_outputs(DEFAULT_CONF)


def test_same_schema_replay_from_alternate_roots(tmp_path):
    """Check alternate roots with wraparound PNs, duplicate handling, gaps, and no hardcoding."""
    root = tmp_path / "case"
    out = tmp_path / "out"
    write_case(
        root,
        "delta",
        [
            make_packet("cid-x", "client", 1, 0, 510, 1, [make_frame(4, 0, b"aa")]),
            make_packet("cid-x", "client", 1, 0, 511, 1, [make_frame(4, 2, b"bb")]),
            make_packet("cid-x", "client", 1, 1, 512, 1, [make_frame(4, 4, b"cc", True)]),
            make_packet("cid-x", "server", 1, 0, 88, 1, [make_frame(4, 0, b"reply", True)]),
            make_packet("cid-x", "client", 1, 1, 513, 1, [make_frame(8, 90, b"z", True), b"\x00"]),
            make_packet("cid-x", "client", 1, 1, 513, 1, [make_frame(8, 90, b"z", True)]),
        ],
        [
            ev("cid-x", "client", 1, 0, 510),
            ev("cid-x", "client", 1, 0, 511),
            ev("cid-x", "client", 1, 1, 512),
            ev("cid-x", "server", 1, 0, 88),
            ev("cid-x", "client", 1, 1, 513),
        ],
    )
    conf = tmp_path / "runtime.conf"
    conf.write_text(f"capture_root={root / 'captures'}\nqlog_root={root / 'qlogs'}\noutput_dir={out}\n")
    run_tool(conf)
    assert_exact_outputs(conf)


def test_recursive_roots_and_repeated_qlog_events(tmp_path):
    """Check recursive input discovery, earliest qlog timeline use, and replay eligibility."""
    root = tmp_path / "case"
    out = tmp_path / "out"
    write_case(
        root,
        "nested/deep",
        [
            make_packet("cid-deep", "client", 2, 0, 254, 1, [make_frame(9, 0, b"ab")]),
            make_packet("cid-deep", "client", 2, 0, 255, 1, [make_frame(9, 2, b"cd", True)]),
        ],
        [
            ev("cid-deep", "client", 2, 0, 254, "lost"),
            ev("cid-deep", "client", 2, 0, 255),
            ev("cid-deep", "client", 2, 0, 254),
        ],
    )
    conf = tmp_path / "runtime.conf"
    conf.write_text(f"capture_root={root / 'captures'}\nqlog_root={root / 'qlogs'}\noutput_dir={out}\n")
    run_tool(conf)
    assert_exact_outputs(conf)


def test_runtime_cleans_stale_output(tmp_path):
    """Verify output_dir is fully recreated and all four artifacts match the reference."""
    cfg = read_conf(DEFAULT_CONF)
    outdir = Path(cfg["output_dir"])
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "stale.tmp").write_text("old")
    run_tool(DEFAULT_CONF)
    assert "stale.tmp" not in {p.name for p in outdir.iterdir()}
    assert_exact_outputs(DEFAULT_CONF)
