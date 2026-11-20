import hashlib
import json
import os
import struct
import subprocess
import zlib
from pathlib import Path

ROOT = Path(os.environ.get("TASK_ROOT", "/app/task_file"))
TESTS_ROOT = Path(os.environ.get("TESTS_ROOT", "/tests"))
MAGIC = b"BWL2SEG\n"

EXPECTED_TEST_HASHES = {
    TESTS_ROOT / "test.sh": "ca428ab64c1961b6f0fdead31746fef24100dd7a321d31e5897cd3e85f95d2e1",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_tests_integrity():
    """The shell verifier should still be the shipped offline pytest runner."""
    for path, want in EXPECTED_TEST_HASHES.items():
        assert path.exists(), f"missing {path}"
        assert _sha256(path) == want, f"{path} was modified"


def _cargo(args, timeout=240):
    env = {**os.environ, "CARGO_TERM_COLOR": "never"}
    return subprocess.run(
        ["cargo", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _build() -> Path:
    result = _cargo(["build"])
    assert result.returncode == 0, result.stdout + result.stderr
    binary = ROOT / "target" / "debug" / "walfix"
    assert binary.exists()
    return binary


def _frame(seq, op, key, value="", committed=True, corrupt_crc=False):
    key_b = key.encode()
    value_b = value.encode()
    payload = key_b + value_b
    flags = 1 if committed else 0
    head16 = struct.pack("<IQBBH", len(payload), seq, op, flags, len(key_b))
    crc = zlib.crc32(head16 + payload) & 0xFFFFFFFF
    if corrupt_crc:
        crc ^= 0x01020304
    return head16 + struct.pack("<I", crc) + payload


def _raw_frame(seq, op, key_b, value_b=b"", committed=True, key_len=None):
    payload = key_b + value_b
    flags = 1 if committed else 0
    if key_len is None:
        key_len = len(key_b)
    head16 = struct.pack("<IQBBH", len(payload), seq, op, flags, key_len)
    crc = zlib.crc32(head16 + payload) & 0xFFFFFFFF
    return head16 + struct.pack("<I", crc) + payload


def _snapshot(seq, items, committed=True):
    payload = bytearray(struct.pack("<H", len(items)))
    for key, value in items:
        key_b = key.encode()
        value_b = value.encode()
        payload += struct.pack("<HI", len(key_b), len(value_b))
        payload += key_b + value_b
    flags = 1 if committed else 0
    head16 = struct.pack("<IQBBH", len(payload), seq, 3, flags, 0)
    crc = zlib.crc32(head16 + payload) & 0xFFFFFFFF
    return head16 + struct.pack("<I", crc) + payload


def _segment(path: Path, segment_id: int, base_seq: int, frames, tail=b""):
    blob = bytearray()
    blob += MAGIC
    blob += struct.pack("<QQ", segment_id, base_seq)
    for frame in frames:
        blob += frame
    blob += tail
    path.write_bytes(blob)
    return path


def _run_replay(binary: Path, *segments: Path):
    return subprocess.run(
        [str(binary), "replay", "--json", *map(str, segments)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _replay(binary: Path, *segments: Path):
    result = _run_replay(binary, *segments)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_cargo_unit_tests_pass():
    """The Rust unit tests must keep the low-level utility behavior intact."""
    result = _cargo(["test"])
    assert result.returncode == 0, result.stdout + result.stderr


def test_replay_orders_by_log_metadata_and_ignores_uncommitted(tmp_path):
    """Recovery order is based on sequence and segment id, not argv order."""
    binary = _build()
    early = _segment(
        tmp_path / "segment-0001.bwl",
        1,
        1,
        [
            _frame(1, 1, "alpha", "old"),
            _frame(2, 1, "beta", "ghost", committed=False),
            _frame(3, 1, "beta", "kept"),
            _frame(7, 2, "alpha"),
        ],
    )
    late = _segment(
        tmp_path / "segment-0009.bwl",
        9,
        3,
        [
            _frame(4, 1, "gamma", "visible"),
            _frame(7, 1, "alpha", "resurrected"),
        ],
    )

    assert _replay(binary, late, early) == {
        "alpha": "resurrected",
        "beta": "kept",
        "gamma": "visible",
    }


def test_bad_frame_only_truncates_its_own_segment(tmp_path):
    """A torn write stops one segment scan but must not poison other segments."""
    binary = _build()
    torn = _segment(
        tmp_path / "segment-0002.bwl",
        2,
        10,
        [
            _frame(10, 1, "before", "ok"),
            _frame(11, 1, "after_bad_crc", "nope", corrupt_crc=True),
            _frame(12, 1, "after_bad_crc", "also_nope"),
        ],
    )
    other = _segment(
        tmp_path / "segment-0003.bwl",
        3,
        12,
        [
            _frame(12, 1, "later", "ok"),
        ],
    )

    assert _replay(binary, torn, other) == {"before": "ok", "later": "ok"}


def test_unknown_operation_stops_only_that_segment(tmp_path):
    """An unknown operation is a local segment boundary, not a global failure."""
    binary = _build()
    damaged = _segment(
        tmp_path / "segment-0010.bwl",
        10,
        30,
        [
            _frame(30, 1, "before_unknown", "kept"),
            _frame(31, 99, "bad", "ignored"),
            _frame(32, 1, "after_unknown", "not_scanned"),
        ],
    )
    other = _segment(
        tmp_path / "segment-0011.bwl",
        11,
        30,
        [_frame(33, 1, "other", "ok")],
    )

    assert _replay(binary, other, damaged) == {
        "before_unknown": "kept",
        "other": "ok",
    }


def test_invalid_utf8_stops_only_that_segment(tmp_path):
    """Malformed text fields preserve earlier records and do not halt replay."""
    binary = _build()
    damaged = _segment(
        tmp_path / "segment-0020.bwl",
        20,
        40,
        [
            _frame(40, 1, "clean", "kept"),
            _raw_frame(41, 1, b"bad\xffkey", b"value"),
            _frame(42, 1, "after_bad_text", "not_scanned"),
        ],
    )
    other = _segment(
        tmp_path / "segment-0021.bwl",
        21,
        40,
        [_frame(43, 1, "still", "recovered")],
    )

    assert _replay(binary, damaged, other) == {
        "clean": "kept",
        "still": "recovered",
    }


def test_sequence_below_segment_base_is_a_local_boundary(tmp_path):
    """A sequence rewind inside one segment must not erase other segment data."""
    binary = _build()
    damaged = _segment(
        tmp_path / "segment-0030.bwl",
        30,
        100,
        [
            _frame(100, 1, "before_rewind", "kept"),
            _frame(99, 1, "rewound", "bad"),
            _frame(101, 1, "after_rewind", "not_scanned"),
        ],
    )
    other = _segment(
        tmp_path / "segment-0031.bwl",
        31,
        100,
        [_frame(101, 1, "neighbor", "kept")],
    )

    assert _replay(binary, other, damaged) == {
        "before_rewind": "kept",
        "neighbor": "kept",
    }


def test_uncommitted_payload_is_validated_before_ignoring(tmp_path):
    """Malformed uncommitted frames still stop scanning their segment."""
    binary = _build()
    damaged = _segment(
        tmp_path / "segment-0035.bwl",
        35,
        200,
        [
            _frame(200, 1, "before_uncommitted_bad", "kept"),
            _raw_frame(201, 1, b"bad\xffkey", b"value", committed=False),
            _frame(202, 1, "after_uncommitted_bad", "not_scanned"),
        ],
    )
    other = _segment(
        tmp_path / "segment-0036.bwl",
        36,
        200,
        [_frame(202, 1, "other_segment", "kept")],
    )

    assert _replay(binary, damaged, other) == {
        "before_uncommitted_bad": "kept",
        "other_segment": "kept",
    }


def test_snapshot_replaces_state_in_global_order(tmp_path):
    """Committed snapshot records participate in the same ordering as frames."""
    binary = _build()
    early = _segment(
        tmp_path / "segment-0001.bwl",
        1,
        1,
        [
            _frame(1, 1, "alpha", "old"),
            _frame(5, 1, "beta", "before_snapshot"),
        ],
    )
    snapshot = _segment(
        tmp_path / "segment-0002.bwl",
        2,
        1,
        [
            _snapshot(5, [("alpha", "snap"), ("gamma", "drop_me")]),
            _frame(6, 1, "beta", "after_snapshot"),
        ],
    )
    late = _segment(
        tmp_path / "segment-0003.bwl",
        3,
        1,
        [
            _frame(7, 2, "gamma"),
            _frame(8, 1, "omega", "z"),
        ],
    )

    assert _replay(binary, late, snapshot, early) == {
        "alpha": "snap",
        "beta": "after_snapshot",
        "omega": "z",
    }


def test_bad_segment_header_aborts_replay(tmp_path):
    """A corrupt segment header is a fatal input error, unlike a bad frame tail."""
    binary = _build()
    good = _segment(
        tmp_path / "segment-0050.bwl",
        50,
        1,
        [_frame(1, 1, "good", "kept")],
    )
    bad = tmp_path / "segment-bad-header.bwl"
    bad.write_bytes(b"not-a-bwl-segment")

    result = _run_replay(binary, good, bad)
    assert result.returncode != 0


def test_truncated_tail_is_recoverable_and_delete_is_applied(tmp_path):
    """A partial trailing header is ignored after prior committed frames."""
    binary = _build()
    seg = _segment(
        tmp_path / "segment-0042.bwl",
        42,
        100,
        [
            _frame(100, 1, "drop", "value"),
            _frame(101, 1, "wide", "x" * 513),
            _frame(102, 2, "drop"),
        ],
        tail=struct.pack("<I", 20),
    )

    assert _replay(binary, seg) == {"wide": "x" * 513}
