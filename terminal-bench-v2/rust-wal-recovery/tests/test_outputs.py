import hashlib
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path

ATTEST = "/usr/local/sbin/store-evidence-attest"


def snapshot(path, generation, last_seq, accounts):
    body = bytearray(b"SST1")
    body += struct.pack("<IQI", generation, last_seq, len(accounts))
    for key, value in accounts.items():
        raw = key.encode()
        body += struct.pack("B", len(raw)) + raw + struct.pack("<q", value)
    (path / "snapshot.bin").write_bytes(body)


def snapshot_entries(path, generation, last_seq, entries):
    body = bytearray(b"SST1")
    body += struct.pack("<IQI", generation, last_seq, len(entries))
    for key, value in entries:
        raw = key.encode()
        body += struct.pack("B", len(raw)) + raw + struct.pack("<q", value)
    (path / "snapshot.bin").write_bytes(body)


def rec(generation, seq, op, key, value, *, checksum_delta=0):
    raw_key = key.encode()
    payload = struct.pack("<IQBB", generation, seq, op, len(raw_key)) + raw_key + struct.pack("<q", value)
    checksum = (sum(payload) + checksum_delta) & 0xFFFFFFFF
    return b"WR" + struct.pack("<H", len(payload)) + payload + struct.pack("<I", checksum)


def rec_with_extra_payload(generation, seq, op, key, value, extra):
    raw_key = key.encode()
    payload = struct.pack("<IQBB", generation, seq, op, len(raw_key)) + raw_key + struct.pack("<q", value) + extra
    checksum = sum(payload) & 0xFFFFFFFF
    return b"WR" + struct.pack("<H", len(payload)) + payload + struct.pack("<I", checksum)


def anchors(path, entries, *, checksum_delta=0, trailing=b""):
    body = bytearray(b"ANC1") + struct.pack("<I", len(entries))
    for seq, key, value in entries:
        raw_key = key.encode()
        entry = struct.pack("<QB", seq, len(raw_key)) + raw_key + struct.pack("<q", value)
        checksum = (sum(entry) + checksum_delta) & 0xFFFFFFFF
        body += entry + struct.pack("<I", checksum)
    (path / "anchors.bin").write_bytes(body + trailing)


def quarantines(path, entries, *, checksum_delta=0, trailing=b""):
    body = bytearray(b"QRN1") + struct.pack("<I", len(entries))
    for generation, start_seq, end_seq, key in entries:
        raw_key = key.encode()
        entry = struct.pack("<IQQB", generation, start_seq, end_seq, len(raw_key)) + raw_key
        checksum = (sum(entry) + checksum_delta) & 0xFFFFFFFF
        body += entry + struct.pack("<I", checksum)
    (path / "quarantine.bin").write_bytes(body + trailing)


def limits(path, entries, *, checksum_delta=0, trailing=b""):
    body = bytearray(b"LIM1") + struct.pack("<I", len(entries))
    for key, minimum, maximum in entries:
        raw_key = key.encode()
        entry = struct.pack("B", len(raw_key)) + raw_key + struct.pack("<qq", minimum, maximum)
        checksum = (sum(entry) + checksum_delta) & 0xFFFFFFFF
        body += entry + struct.pack("<I", checksum)
    (path / "limits.bin").write_bytes(body + trailing)


def leases(path, entries, *, checksum_delta=0, trailing=b""):
    body = bytearray(b"LSE1") + struct.pack("<I", len(entries))
    for generation, start_seq, end_seq, key in entries:
        raw_key = key.encode()
        entry = struct.pack("<IQQB", generation, start_seq, end_seq, len(raw_key)) + raw_key
        checksum = (sum(entry) + checksum_delta) & 0xFFFFFFFF
        body += entry + struct.pack("<I", checksum)
    (path / "leases.bin").write_bytes(body + trailing)


def rollups(path, entries, *, checksum_delta=0, trailing=b""):
    body = bytearray(b"RLP1") + struct.pack("<I", len(entries))
    for generation, start_seq, end_seq, key, expected_delta in entries:
        raw_key = key.encode()
        entry = struct.pack("<IQQB", generation, start_seq, end_seq, len(raw_key)) + raw_key + struct.pack(
            "<q", expected_delta
        )
        checksum = (sum(entry) + checksum_delta) & 0xFFFFFFFF
        body += entry + struct.pack("<I", checksum)
    (path / "rollups.bin").write_bytes(body + trailing)


def run_attest(store, report):
    env = os.environ.copy()
    env.setdefault("CARGO_TERM_COLOR", "never")
    return subprocess.run(
        [ATTEST, str(store), str(report)],
        cwd=Path("/tmp"),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def load_report(path):
    return json.loads(path.read_text())


def app_fingerprint():
    root = Path("/app")
    entries = []
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        rel = path.relative_to(root).as_posix()
        kind = "d" if path.is_dir() else "f"
        entries.append((rel, kind, stat.st_size, stat.st_mtime_ns))
    return entries


def state_fingerprint(root):
    entries = []
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            entries.append((rel, "d", stat.st_size, stat.st_mtime_ns, None))
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((rel, "f", stat.st_size, stat.st_mtime_ns, digest))
    return entries


def test_recovery_case_a():
    """Checks mixed-generation replay, duplicate sequences, sorting, and LE WAL lengths."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "report.json"
        snapshot(store, 7, 10, {"alpha": 40, "beta": 5})
        wal = b"".join(
            [
                rec(7, 12, 2, "alpha", 5),
                rec(7, 11, 2, "beta", 7),
                rec(7, 12, 2, "alpha", 5),
                rec(8, 13, 2, "alpha", 99),
                rec(7, 14, 1, "gamma", 33),
                rec(7, 9, 2, "beta", 100),
            ]
        )
        (store / "wal.log").write_bytes(wal)

        result = run_attest(store, report)
        assert result.returncode == 0, result.stderr
        data = load_report(report)
        assert data == {
            "verdict": "accepted",
            "generation": 7,
            "last_seq": 14,
            "accounts": {"alpha": 45, "beta": 12, "gamma": 33},
            "findings": [],
        }


def test_recovery_case_b():
    """Checks recovery past garbage bytes, bad checksums, and trailing partial frames."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "report.json"
        snapshot(store, 3, 2, {"core": 10, "edge": 1})
        wal = bytearray()
        wal += rec(3, 3, 2, "core", 4)
        wal += b"\x00broken-sector"
        wal += rec(3, 4, 2, "edge", 8)
        wal += rec(3, 5, 2, "core", 100, checksum_delta=1)
        wal += b"gap"
        wal += rec(3, 6, 3, "edge", 0)
        wal += rec(3, 7, 1, "late", 22)[:9]
        (store / "wal.log").write_bytes(wal)

        result = run_attest(store, report)
        assert result.returncode == 0, result.stderr
        data = load_report(report)
        assert data["verdict"] == "accepted"
        assert data["generation"] == 3
        assert data["last_seq"] == 6
        assert data["accounts"] == {"core": 14}
        assert data["findings"] == []


def test_recovery_case_no_wal_log():
    """Checks that wal.log is optional and a snapshot-only store still reports cleanly."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "report.json"
        snapshot(store, 6, 31, {"cold": 4, "warm": 9})

        result = run_attest(store, report)
        assert result.returncode == 0, result.stderr
        assert load_report(report) == {
            "verdict": "accepted",
            "generation": 6,
            "last_seq": 31,
            "accounts": {"cold": 4, "warm": 9},
            "findings": [],
        }


def test_recovery_case_c():
    """Checks that negative accepted activity exits nonzero without a report."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "out" / "report.json"
        snapshot(store, 5, 20, {"tenant-a": 3, "tenant-b": 2})
        (store / "wal.log").write_bytes(rec(5, 21, 2, "tenant-a", -4) + rec(5, 22, 2, "tenant-b", 9))

        result = run_attest(store, report)
        assert result.returncode != 0
        assert not report.exists()


def test_recovery_case_d():
    """Checks deterministic account-object output for escaped and long account keys."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "report.json"
        special = 'ops"node\\1'
        snapshot(store, 1, 0, {special: 6})
        long_key = "n" * 240
        wal = rec(1, 1, 2, special, 9) + rec(1, 2, 1, long_key, 17)
        (store / "wal.log").write_bytes(wal)

        result = run_attest(store, report)
        assert result.returncode == 0, result.stderr
        data = load_report(report)
        assert data["verdict"] == "accepted"
        assert data["accounts"][special] == 15
        assert data["accounts"][long_key] == 17
        assert list(data["accounts"].keys()) == sorted(data["accounts"].keys())


def test_recovery_case_e():
    """Checks findings for accepted unknown ops and rejects non-exact WAL payloads."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "report.json"
        snapshot(store, 11, 40, {"cash": 10})
        wal = b"".join(
            [
                rec_with_extra_payload(11, 41, 2, "cash", 90, b"shadow"),
                rec(11, 42, 99, "cash", 0),
                rec(12, 43, 99, "cash", 0),
                rec(11, 44, 2, "cash", 5),
            ]
        )
        (store / "wal.log").write_bytes(wal)

        result = run_attest(store, report)
        assert result.returncode == 0, result.stderr
        data = load_report(report)
        assert data == {
            "verdict": "accepted",
            "generation": 11,
            "last_seq": 44,
            "accounts": {"cash": 15},
            "findings": ["ignored-op:42"],
        }


def test_recovery_case_f():
    """Checks that invocation writes only the report path and does not mutate /app or inputs."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "reports" / "nested" / "report.json"
        snapshot(store, 2, 0, {"vault": 8})
        (store / "wal.log").write_bytes(rec(2, 1, 2, "vault", 4))
        os.utime(Path("/app/src/main.rs"), None)
        before = app_fingerprint()
        state_before = state_fingerprint(store)

        result = run_attest(store, report)
        assert result.returncode == 0, result.stderr
        assert load_report(report)["accounts"] == {"vault": 12}
        assert app_fingerprint() == before
        assert state_fingerprint(store) == state_before
        assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if not p.is_relative_to(store)) == [
            "reports",
            "reports/nested",
            "reports/nested/report.json",
        ]


def test_recovery_case_g():
    """Checks resync after misleading frame headers, first duplicate wins, and ordered findings."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "report.json"
        snapshot(store, 4, 5, {"cold": 20, "hot": 7})
        wal = b"".join(
            [
                b"WR" + struct.pack("<H", 400) + b"discarded-fragment",
                rec(4, 8, 2, "cold", 5),
                b"\xaa",
                rec(4, 6, 3, "ghost", 0),
                rec(4, 7, 2, "hot", 3),
                rec(4, 7, 2, "hot", 100),
                rec(4, 9, 77, "cold", 0),
            ]
        )
        (store / "wal.log").write_bytes(wal)

        result = run_attest(store, report)
        assert result.returncode == 0, result.stderr
        assert load_report(report) == {
            "verdict": "accepted",
            "generation": 4,
            "last_seq": 9,
            "accounts": {"cold": 25, "hot": 10},
            "findings": ["missing-delete:6", "ignored-op:9"],
        }


def test_recovery_case_checkpoint_and_duplicate_key_guards():
    """Checks checkpoint findings and duplicate snapshot-key rejection."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        checkpoint_store = root / "checkpoint"
        checkpoint_store.mkdir()
        checkpoint_report = root / "checkpoint.json"
        snapshot(checkpoint_store, 13, 100, {"safe": 50, "reserve": 7})
        wal = b"".join(
            [
                rec(13, 101, 2, "safe", 5),
                rec(99, 102, 4, "_total", 999),
                rec(13, 102, 4, "_total", 62),
                rec(13, 103, 4, "_total", 63),
                rec(13, 104, 3, "reserve", 0),
                rec(13, 105, 4, "_total", 55),
            ]
        )
        (checkpoint_store / "wal.log").write_bytes(wal)

        result = run_attest(checkpoint_store, checkpoint_report)
        assert result.returncode == 0, result.stderr
        assert load_report(checkpoint_report) == {
            "verdict": "accepted",
            "generation": 13,
            "last_seq": 105,
            "accounts": {"safe": 55},
            "findings": ["checkpoint-mismatch:103"],
        }

        duplicate_store = root / "duplicate"
        duplicate_store.mkdir()
        duplicate_report = root / "duplicate.json"
        snapshot_entries(duplicate_store, 13, 0, [("dup", 1), ("dup", 2)])
        duplicate_result = run_attest(duplicate_store, duplicate_report)
        assert duplicate_result.returncode != 0
        assert not duplicate_report.exists()


def test_recovery_case_anchor_attestations_and_checkpoint_non_total():
    """Checks anchor comparison after replay and non-_total checkpoint findings."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "report.json"
        snapshot(store, 21, 4, {"alpha": 8, "beta": 3})
        wal = b"".join(
            [
                rec(21, 5, 2, "alpha", 7),
                rec(21, 6, 4, "sector", 0),
                rec(21, 7, 1, "beta", 11),
                rec(21, 8, 2, "gamma", 2),
            ]
        )
        (store / "wal.log").write_bytes(wal)
        anchors(
            store,
            [
                (5, "alpha", 15),
                (7, "beta", 10),
                (8, "gamma", 2),
                (9, "alpha", 999),
                (8, "missing", 0),
            ],
        )

        result = run_attest(store, report)
        assert result.returncode == 0, result.stderr
        assert load_report(report) == {
            "verdict": "accepted",
            "generation": 21,
            "last_seq": 8,
            "accounts": {"alpha": 15, "beta": 11, "gamma": 2},
            "findings": ["ignored-op:6", "anchor-mismatch:7:beta"],
        }


def test_recovery_case_anchor_file_integrity_and_exact_consumption():
    """Checks anchor checksum validation and exact anchor-file consumption."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        bad_checksum = root / "bad-checksum"
        bad_checksum.mkdir()
        bad_checksum_report = root / "bad-checksum.json"
        snapshot(bad_checksum, 30, 1, {"sealed": 4})
        anchors(bad_checksum, [(1, "sealed", 4)], checksum_delta=1)
        bad_checksum_result = run_attest(bad_checksum, bad_checksum_report)
        assert bad_checksum_result.returncode != 0
        assert not bad_checksum_report.exists()

        trailing = root / "trailing-anchor"
        trailing.mkdir()
        trailing_report = root / "trailing-anchor.json"
        snapshot(trailing, 30, 1, {"sealed": 4})
        anchors(trailing, [(1, "sealed", 4)], trailing=b"extra")
        trailing_result = run_attest(trailing, trailing_report)
        assert trailing_result.returncode != 0
        assert not trailing_report.exists()


def test_recovery_case_h():
    """Checks malformed snapshots and negative snapshot balances are rejected without reports."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        trailing_store = root / "trailing"
        trailing_store.mkdir()
        trailing_report = root / "trailing.json"
        snapshot(trailing_store, 1, 0, {"clean": 1})
        with (trailing_store / "snapshot.bin").open("ab") as fh:
            fh.write(b"extra")
        trailing_result = run_attest(trailing_store, trailing_report)
        assert trailing_result.returncode != 0
        assert not trailing_report.exists()

        negative_store = root / "negative"
        negative_store.mkdir()
        negative_report = root / "negative.json"
        snapshot(negative_store, 1, 0, {"bad": -1})
        negative_result = run_attest(negative_store, negative_report)
        assert negative_result.returncode != 0
        assert not negative_report.exists()


def test_recovery_case_i():
    """Checks accepted arithmetic overflow and negative set results are rejected without reports."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        overflow_store = root / "overflow"
        overflow_store.mkdir()
        overflow_report = root / "overflow.json"
        snapshot(overflow_store, 9, 0, {"limit": (2**63) - 2})
        (overflow_store / "wal.log").write_bytes(rec(9, 1, 2, "limit", 3))
        overflow_result = run_attest(overflow_store, overflow_report)
        assert overflow_result.returncode != 0
        assert not overflow_report.exists()

        set_store = root / "set-negative"
        set_store.mkdir()
        set_report = root / "set-negative.json"
        snapshot(set_store, 9, 0, {"ok": 5})
        (set_store / "wal.log").write_bytes(rec(9, 1, 1, "ok", -4))
        set_result = run_attest(set_store, set_report)
        assert set_result.returncode != 0
        assert not set_report.exists()


def test_recovery_case_quarantine_replay_order_and_anchors():
    """Checks quarantined records are skipped after duplicate selection and before anchors."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "report.json"
        snapshot(store, 42, 10, {"hot": 20, "cold": 4, "watch": 1})
        wal = b"".join(
            [
                rec(42, 11, 2, "hot", 5),
                rec(42, 12, 2, "cold", 7),
                rec(42, 12, 2, "cold", 99),
                rec(42, 13, 4, "_total", 30),
                rec(42, 14, 2, "watch", 3),
                rec(43, 15, 2, "hot", 100),
                rec(42, 16, 2, "hot", 1),
            ]
        )
        (store / "wal.log").write_bytes(wal)
        quarantines(store, [(42, 12, 14, "cold"), (42, 14, 16, "hot"), (43, 15, 15, "hot")])
        anchors(store, [(11, "hot", 25), (16, "hot", 26), (14, "watch", 4)])

        result = run_attest(store, report)
        assert result.returncode == 0, result.stderr
        assert load_report(report) == {
            "verdict": "accepted",
            "generation": 42,
            "last_seq": 14,
            "accounts": {"cold": 4, "hot": 25, "watch": 4},
            "findings": ["quarantined:12:cold", "quarantined:16:hot"],
        }


def test_recovery_case_quarantine_integrity_rejections():
    """Checks malformed quarantine sidecars fail without creating a report."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        bad_checksum = root / "bad-checksum"
        bad_checksum.mkdir()
        bad_checksum_report = root / "bad-checksum.json"
        snapshot(bad_checksum, 50, 0, {"a": 1})
        quarantines(bad_checksum, [(50, 1, 2, "a")], checksum_delta=1)
        bad_checksum_result = run_attest(bad_checksum, bad_checksum_report)
        assert bad_checksum_result.returncode != 0
        assert not bad_checksum_report.exists()

        trailing = root / "trailing"
        trailing.mkdir()
        trailing_report = root / "trailing.json"
        snapshot(trailing, 50, 0, {"a": 1})
        quarantines(trailing, [(50, 1, 2, "a")], trailing=b"x")
        trailing_result = run_attest(trailing, trailing_report)
        assert trailing_result.returncode != 0
        assert not trailing_report.exists()

        inverted = root / "inverted"
        inverted.mkdir()
        inverted_report = root / "inverted.json"
        snapshot(inverted, 50, 0, {"a": 1})
        quarantines(inverted, [(50, 9, 3, "a")])
        inverted_result = run_attest(inverted, inverted_report)
        assert inverted_result.returncode != 0
        assert not inverted_report.exists()


def test_recovery_case_limits_enforced_after_replay_selection():
    """Checks limits are enforced after duplicate selection and quarantine skipping."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        ok_store = root / "ok"
        ok_store.mkdir()
        ok_report = root / "ok.json"
        snapshot(ok_store, 60, 0, {"capped": 8, "free": 1})
        limits(ok_store, [("capped", 0, 12)])
        quarantines(ok_store, [(60, 3, 3, "capped")])
        (ok_store / "wal.log").write_bytes(
            rec(60, 1, 2, "capped", 3)
            + rec(60, 2, 2, "free", 20)
            + rec(60, 3, 2, "capped", 99)
            + rec(60, 4, 4, "_total", 32)
        )

        ok_result = run_attest(ok_store, ok_report)
        assert ok_result.returncode == 0, ok_result.stderr
        assert load_report(ok_report) == {
            "verdict": "accepted",
            "generation": 60,
            "last_seq": 4,
            "accounts": {"capped": 11, "free": 21},
            "findings": ["quarantined:3:capped"],
        }

        bad_store = root / "bad"
        bad_store.mkdir()
        bad_report = root / "bad.json"
        snapshot(bad_store, 60, 0, {"capped": 8})
        limits(bad_store, [("capped", 0, 12)])
        (bad_store / "wal.log").write_bytes(rec(60, 1, 2, "capped", 3) + rec(60, 2, 2, "capped", 2))
        bad_result = run_attest(bad_store, bad_report)
        assert bad_result.returncode != 0
        assert not bad_report.exists()


def test_recovery_case_limits_integrity_rejections():
    """Checks malformed limits sidecars fail without creating a report."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        bad_checksum = root / "bad-checksum"
        bad_checksum.mkdir()
        bad_checksum_report = root / "bad-checksum.json"
        snapshot(bad_checksum, 61, 0, {"a": 1})
        limits(bad_checksum, [("a", 0, 10)], checksum_delta=1)
        bad_checksum_result = run_attest(bad_checksum, bad_checksum_report)
        assert bad_checksum_result.returncode != 0
        assert not bad_checksum_report.exists()

        duplicate = root / "duplicate"
        duplicate.mkdir()
        duplicate_report = root / "duplicate.json"
        snapshot(duplicate, 61, 0, {"a": 1})
        limits(duplicate, [("a", 0, 10), ("a", 0, 11)])
        duplicate_result = run_attest(duplicate, duplicate_report)
        assert duplicate_result.returncode != 0
        assert not duplicate_report.exists()

        inverted = root / "inverted"
        inverted.mkdir()
        inverted_report = root / "inverted.json"
        snapshot(inverted, 61, 0, {"a": 1})
        limits(inverted, [("a", 9, 3)])
        inverted_result = run_attest(inverted, inverted_report)
        assert inverted_result.returncode != 0
        assert not inverted_report.exists()


def test_recovery_case_leases_rollups_and_anchor_order():
    """Checks leased replay skips, rollup deltas, limit bypass, and post-rollup anchor order."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "store"
        store.mkdir()
        report = root / "report.json"
        snapshot(store, 70, 4, {"vault": 10, "free": 3, "spent": 9})
        (store / "wal.log").write_bytes(
            rec(70, 5, 2, "vault", 2)
            + rec(70, 6, 2, "vault", 99)
            + rec(70, 6, 2, "vault", 4)
            + rec(70, 7, 2, "free", 5)
            + rec(70, 8, 3, "spent", 0)
            + rec(70, 9, 2, "vault", 1)
            + rec(70, 10, 4, "_total", 15)
        )
        limits(store, [("vault", 0, 20)])
        quarantines(store, [(70, 7, 7, "free")])
        leases(store, [(70, 6, 9, "vault")])
        rollups(store, [(70, 5, 9, "vault", 2), (70, 5, 9, "free", 4), (71, 5, 9, "vault", 999)])
        anchors(store, [(10, "vault", 12), (10, "spent", 0)])

        result = run_attest(store, report)
        assert result.returncode == 0, result.stderr
        assert load_report(report) == {
            "verdict": "accepted",
            "generation": 70,
            "last_seq": 10,
            "accounts": {"free": 3, "vault": 12},
            "findings": [
                "leased:6:vault",
                "quarantined:7:free",
                "leased:9:vault",
                "rollup-mismatch:5:9:free",
            ],
        }


def test_recovery_case_lease_and_rollup_integrity_rejections():
    """Checks malformed lease and rollup sidecars fail without creating reports."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        bad_lease = root / "bad-lease"
        bad_lease.mkdir()
        bad_lease_report = root / "bad-lease.json"
        snapshot(bad_lease, 80, 0, {"a": 1})
        leases(bad_lease, [(80, 1, 2, "a")], checksum_delta=1)
        bad_lease_result = run_attest(bad_lease, bad_lease_report)
        assert bad_lease_result.returncode != 0
        assert not bad_lease_report.exists()

        trailing_rollup = root / "trailing-rollup"
        trailing_rollup.mkdir()
        trailing_rollup_report = root / "trailing-rollup.json"
        snapshot(trailing_rollup, 80, 0, {"a": 1})
        rollups(trailing_rollup, [(80, 1, 2, "a", 0)], trailing=b"x")
        trailing_rollup_result = run_attest(trailing_rollup, trailing_rollup_report)
        assert trailing_rollup_result.returncode != 0
        assert not trailing_rollup_report.exists()

        duplicate_rollup = root / "duplicate-rollup"
        duplicate_rollup.mkdir()
        duplicate_rollup_report = root / "duplicate-rollup.json"
        snapshot(duplicate_rollup, 80, 0, {"a": 1})
        rollups(duplicate_rollup, [(80, 1, 3, "a", 1), (80, 1, 3, "a", 2)])
        duplicate_rollup_result = run_attest(duplicate_rollup, duplicate_rollup_report)
        assert duplicate_rollup_result.returncode != 0
        assert not duplicate_rollup_report.exists()
