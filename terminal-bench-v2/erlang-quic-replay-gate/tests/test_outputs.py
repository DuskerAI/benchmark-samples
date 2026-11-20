import json
import os
import struct
import subprocess
from pathlib import Path

APP = Path("/opt/quic_gate")
BIN = APP / "bin" / "quic_gate"


def qv(n: int) -> bytes:
    if n < 64:
        return bytes([n])
    if n < 16384:
        return (0x4000 | n).to_bytes(2, "big")
    if n < 1073741824:
        return (0x80000000 | n).to_bytes(4, "big")
    return (0xC000000000000000 | n).to_bytes(8, "big")


def pkt(epoch: int, phase: int, dcid: bytes, pn: int, width: int) -> bytes:
    trunc = pn & ((1 << (8 * width)) - 1)
    body = (
        bytes([epoch, phase])
        + qv(len(dcid))
        + dcid
        + bytes([width])
        + trunc.to_bytes(width, "big")
    )
    return struct.pack("!H", len(body)) + body


def raw(body: bytes) -> bytes:
    return struct.pack("!H", len(body)) + body


def run_gate(tmp_path: Path, trace: bytes, state: Path | None = None) -> dict:
    subprocess.run(["make", "-C", str(APP)], check=True, capture_output=True)
    input_path = tmp_path / ("trace_" + next_name(tmp_path) + ".bin")
    out_path = tmp_path / ("report_" + next_name(tmp_path) + ".json")
    state_path = state or (tmp_path / "state.bin")
    input_path.write_bytes(trace)
    subprocess.run(
        [
            str(BIN),
            "--state",
            str(state_path),
            "--input",
            str(input_path),
            "--out",
            str(out_path),
        ],
        check=True,
        cwd=str(APP),
        capture_output=True,
        env={**os.environ, "ERL_FLAGS": "+S 1"},
    )
    data = out_path.read_bytes()
    assert data.endswith(b"\n")
    return json.loads(data)


def next_name(tmp_path: Path) -> str:
    return str(len(list(tmp_path.iterdir())))


def test_admission_policy_alpha(tmp_path):
    """Admission reports basic verdicts and keeps scanning complete records."""
    trace = b"".join([
        pkt(0, 0, b"a", 0, 1),
        pkt(0, 0, b"a", 0, 1),
        raw(b"\x09"),
        pkt(0, 0, b"a", 1, 1),
    ])
    report = run_gate(tmp_path, trace)
    assert report["status"] == "ok"
    assert report["accepted"] == 2
    assert report["rejected"] == 2
    assert report["reasons"] == {"duplicate": 1, "invalid": 1}
    assert [e["verdict"] for e in report["audit"]] == [
        "accept",
        "reject",
        "reject",
        "accept",
    ]


def test_admission_policy_beta(tmp_path):
    """Admission remains stable over a generated high-number capture."""
    dcid = bytes(range(70))
    trace = b"".join(pkt(0, 0, dcid, pn, 1) for pn in range(250, 257))
    trace += pkt(0, 0, dcid, 260, 4)
    report = run_gate(tmp_path, trace)
    assert report["status"] == "ok"
    assert report["accepted"] == 8
    assert report["rejected"] == 0
    assert [e["pn"] for e in report["audit"]][-3:] == [255, 256, 260]


def test_admission_policy_gamma(tmp_path):
    """Admission separates security state by the visible packet space."""
    dcid = b"same"
    trace = b"".join([
        pkt(0, 0, dcid, 7, 1),
        pkt(1, 0, dcid, 7, 1),
        pkt(2, 0, dcid, 7, 1),
        pkt(2, 1, dcid, 7, 1),
        pkt(2, 1, dcid, 7, 1),
    ])
    report = run_gate(tmp_path, trace)
    assert report["accepted"] == 4
    assert report["rejected"] == 1
    assert report["reasons"] == {"duplicate": 1}
    assert [e["epoch"] for e in report["audit"][:4]] == [0, 1, 2, 2]


def test_admission_policy_delta(tmp_path):
    """Admission persists only completed captures while retaining the moving window."""
    state = tmp_path / "state.bin"
    dcid = b"persist"
    first = b"".join([
        pkt(2, 1, dcid, 100, 4),
        pkt(2, 1, dcid, 99, 1),
        pkt(2, 1, dcid, 99, 1),
        pkt(2, 1, dcid, 36, 1),
    ])
    one = run_gate(tmp_path, first, state)
    assert one["accepted"] == 2
    assert one["rejected"] == 2
    assert one["reasons"] == {"duplicate": 1, "stale": 1}

    two = run_gate(tmp_path, pkt(2, 1, dcid, 100, 1) + pkt(2, 1, dcid, 101, 1), state)
    assert two["accepted"] == 1
    assert two["rejected"] == 1
    assert two["reasons"] == {"duplicate": 1}

    bad_tail = pkt(2, 1, dcid, 102, 1) + b"\x00\x08\x02\x01"
    three = run_gate(tmp_path, bad_tail, state)
    assert three["status"] == "failed"
    assert three["accepted"] == 1

    four = run_gate(tmp_path, pkt(2, 1, dcid, 102, 1), state)
    assert four["status"] == "ok"
    assert four["accepted"] == 1
    assert four["rejected"] == 0
