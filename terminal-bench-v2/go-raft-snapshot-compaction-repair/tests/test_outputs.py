import copy
import hashlib
import json
import os
import shutil
import subprocess
import zlib
from pathlib import Path


APP = Path("/app")
BIN = APP / "bin" / "raft-recover"


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def rec(payload, crc=None):
    body = canon(payload)
    if crc is None:
        crc = f"{zlib.crc32(body.encode()) & 0xffffffff:08x}"
    return canon({"crc": crc, "payload": payload})


def write_case(root, snapshots, segments):
    (root / "snapshots").mkdir(parents=True, exist_ok=True)
    (root / "segments").mkdir(parents=True, exist_ok=True)
    for name, snap in snapshots.items():
        (root / "snapshots" / name).write_text(canon(snap) + "\n")
    for name, lines in segments.items():
        text = "\n".join(lines)
        if text:
            text += "\n"
        (root / "segments" / name).write_text(text)


def build_binary():
    subprocess.run(["bash", "/app/scripts/build.sh"], cwd=APP, check=True)
    assert BIN.exists() and os.access(BIN, os.X_OK)


def run_case(case_dir, out_dir):
    if out_dir.exists():
        shutil.rmtree(out_dir)
    subprocess.run(
        [str(BIN), "recover", "--case", str(case_dir), "--out", str(out_dir)],
        cwd=APP,
        check=True,
    )
    state_path = out_dir / "state.json"
    assert state_path.exists(), "state.json was not written"
    return json.loads(state_path.read_text())


def apply_one(kv, entry):
    op = entry["op"]
    if op == "set":
        kv[entry["key"]] = entry["value"]
    elif op == "incr":
        kv[entry["key"]] = kv.get(entry["key"], 0) + entry["delta"]
    elif op == "del":
        kv.pop(entry["key"], None)
    elif op == "cas":
        if kv.get(entry["key"], 0) == entry["expect"]:
            kv[entry["key"]] = entry["value"]
    elif op == "batch":
        for child in entry.get("ops", []):
            apply_one(kv, child)
    else:
        raise AssertionError(f"unknown operation in generated case: {op}")


def entry_same(a, b):
    return canon(a) == canon(b)


def reference(case_dir):
    snaps = []
    for path in sorted((case_dir / "snapshots").glob("*.json")):
        snaps.append(json.loads(path.read_text()))
    snap = sorted(snaps, key=lambda s: (-s["index"], -s["term"], s["id"]))[0]
    kv = copy.deepcopy(snap.get("kv", {}))
    log = {}
    last_applied = snap["index"]
    applied = []
    ignored = 0

    def term_at(idx):
        if idx == snap["index"]:
            return snap["term"]
        if idx in log:
            return log[idx]["term"]
        return None

    for seg in sorted((case_dir / "segments").glob("*.rlog")):
        segment_text = seg.read_text()
        raw_lines = segment_text.splitlines()
        incomplete_raw_idx = None
        if segment_text and not segment_text.endswith("\n") and raw_lines and raw_lines[-1].strip():
            incomplete_raw_idx = len(raw_lines) - 1
        physical = [
            (ln, raw_idx == incomplete_raw_idx)
            for raw_idx, ln in enumerate(raw_lines)
            if ln.strip()
        ]
        pos = 0
        while pos < len(physical):
            line, incomplete = physical[pos]
            if incomplete:
                ignored += len(physical) - pos
                break
            try:
                obj = json.loads(line)
                payload = obj["payload"]
                want = f"{zlib.crc32(canon(payload).encode()) & 0xffffffff:08x}"
                if obj.get("crc") != want:
                    raise ValueError("bad record")
            except Exception:
                ignored += len(physical) - pos
                break
            if term_at(payload["prev_index"]) == payload["prev_term"]:
                for entry in payload.get("entries", []):
                    idx = entry["index"]
                    if idx <= snap["index"]:
                        continue
                    if idx in log and entry_same(log[idx], entry):
                        continue
                    for old in [k for k in log if k >= idx]:
                        del log[old]
                    log[idx] = entry
                limit = min(payload["leader_commit"], max(log.keys(), default=snap["index"]))
                while last_applied < limit and last_applied + 1 in log:
                    nxt = last_applied + 1
                    apply_one(kv, log[nxt])
                    last_applied = nxt
                    applied.append(nxt)
            pos += 1

    last_term = snap["term"]
    if log:
        last_term = log[max(log)] ["term"]
    digest = hashlib.sha256(canon({"kv": kv, "last_applied": last_applied, "last_term": last_term}).encode()).hexdigest()
    return {
        "snapshot_id": snap["id"],
        "last_applied": last_applied,
        "last_term": last_term,
        "kv": kv,
        "applied": applied,
        "ignored_records": ignored,
        "state_digest": digest,
    }


def assert_state_matches(actual, expected):
    assert set(actual) == set(expected), "state schema mismatch"
    assert actual == expected


def test_public_case_conforms_to_contract(tmp_path):
    """The bundled case must rebuild through the documented command and full output schema."""
    build_binary()
    case_dir = APP / "cases" / "public"
    actual = run_case(case_dir, tmp_path / "out-public")
    expected = reference(case_dir)
    assert_state_matches(actual, expected)


def test_generated_case_set_one(tmp_path):
    """A bad CRC aborts the rest of only that segment; later cas/batch records still replay."""
    build_binary()
    case_dir = tmp_path / "case-one"
    good_after_bad = {
        "node": "b",
        "prev_index": 8,
        "prev_term": 2,
        "leader_commit": 9,
        "entries": [{"index": 9, "term": 3, "op": "set", "key": "z", "value": 99}],
    }
    write_case(
        case_dir,
        {
            "older.json": {"id": "older", "index": 3, "term": 1, "kv": {"a": 1, "x": 7}},
            "newer.json": {"id": "newer", "index": 6, "term": 2, "kv": {"a": 5, "x": 1}},
        },
        {
            "0001.rlog": [
                rec({
                    "node": "a",
                    "prev_index": 6,
                    "prev_term": 2,
                    "leader_commit": 8,
                    "entries": [
                        {"index": 7, "term": 2, "op": "set", "key": "a", "value": 10},
                        {"index": 8, "term": 2, "op": "incr", "key": "a", "delta": 2},
                    ],
                }),
                rec(good_after_bad, crc="00000000"),
                rec(good_after_bad),
            ],
            "0002.rlog": [
                rec({
                    "node": "b",
                    "prev_index": 8,
                    "prev_term": 2,
                    "leader_commit": 10,
                    "entries": [
                        {"index": 9, "term": 3, "op": "cas", "key": "a", "expect": 12, "value": 30},
                        {"index": 10, "term": 3, "op": "batch", "ops": [
                            {"op": "del", "key": "x"},
                            {"op": "incr", "key": "b", "delta": 4},
                        ]},
                    ],
                })
            ],
        },
    )
    actual = run_case(case_dir, tmp_path / "out-one")
    expected = reference(case_dir)
    # Verifies CRC mismatch abort accounting, later segment recovery, cas, del, and nested batch replay.
    assert_state_matches(actual, expected)


def test_generated_case_set_two(tmp_path):
    """Identical duplicates are harmless, but a later conflicting suffix replaces retained entries."""
    build_binary()
    case_dir = tmp_path / "case-two"
    write_case(
        case_dir,
        {"base.json": {"id": "base", "index": 10, "term": 4, "kv": {"k": 1, "carry": 8}}},
        {
            "0001.rlog": [
                rec({
                    "node": "n",
                    "prev_index": 10,
                    "prev_term": 4,
                    "leader_commit": 11,
                    "entries": [
                        {"index": 11, "term": 5, "op": "set", "key": "k", "value": 5},
                        {"index": 12, "term": 5, "op": "incr", "key": "k", "delta": 1},
                        {"index": 13, "term": 5, "op": "set", "key": "carry", "value": 99},
                    ],
                })
            ],
            "0002.rlog": [
                rec({
                    "node": "n",
                    "prev_index": 10,
                    "prev_term": 4,
                    "leader_commit": 11,
                    "entries": [{"index": 11, "term": 5, "op": "set", "key": "k", "value": 5}],
                }),
                rec({
                    "node": "n",
                    "prev_index": 11,
                    "prev_term": 5,
                    "leader_commit": 13,
                    "entries": [
                        {"index": 12, "term": 6, "op": "set", "key": "k", "value": 20},
                        {"index": 13, "term": 6, "op": "incr", "key": "k", "delta": 3},
                    ],
                }),
            ],
        },
    )
    actual = run_case(case_dir, tmp_path / "out-two")
    expected = reference(case_dir)
    # Verifies duplicate detection by canonical entry content and suffix truncation before replay.
    assert_state_matches(actual, expected)


def test_generated_case_set_three(tmp_path):
    """Snapshot tie-breaking, payload-wide CRC scope, and long records all affect recovery."""
    build_binary()
    case_dir = tmp_path / "case-three"
    long_entries = [
        {"index": idx, "term": 5, "op": "incr", "key": "counter", "delta": 1}
        for idx in range(21, 1521)
    ]
    write_case(
        case_dir,
        {
            "000-z.json": {
                "id": "z-snap",
                "index": 20,
                "term": 4,
                "kv": {"counter": 0, "shadow": 3},
            },
            "999-a.json": {
                "id": "a-snap",
                "index": 20,
                "term": 4,
                "kv": {"counter": 10, "shadow": 3},
            },
        },
        {
            "0001.rlog": [
                rec({
                    "node": "wide",
                    "prev_index": 20,
                    "prev_term": 4,
                    "leader_commit": 1520,
                    "entries": long_entries,
                    "wire_epoch": "extension-member-counts-for-crc-only",
                })
            ],
            "0002.rlog": [
                rec({
                    "node": "wide",
                    "prev_index": 1520,
                    "prev_term": 5,
                    "leader_commit": 1522,
                    "entries": [
                        {
                            "index": 1521,
                            "term": 6,
                            "op": "cas",
                            "key": "counter",
                            "expect": 1510,
                            "value": 9007199254740993,
                        },
                        {"index": 1522, "term": 6, "op": "batch", "ops": [
                            {"op": "incr", "key": "counter", "delta": 7},
                            {"op": "set", "key": "marker", "value": 42},
                            {"op": "del", "key": "shadow"},
                        ]},
                    ],
                })
            ],
        },
    )
    actual = run_case(case_dir, tmp_path / "out-three")
    expected = reference(case_dir)
    # Verifies smallest-id snapshot tie-breaking, CRC coverage of extra payload members, and large record handling.
    assert_state_matches(actual, expected)


def test_generated_case_set_four(tmp_path):
    """Heartbeats commit retained suffixes; malformed and incomplete tails abort only their segments."""
    build_binary()
    case_dir = tmp_path / "case-four"
    write_case(
        case_dir,
        {"base.json": {"id": "base", "index": 30, "term": 6, "kv": {"n": 1, "keep": 5}}},
        {
            "0001.rlog": [
                rec({
                    "node": "h",
                    "prev_index": 30,
                    "prev_term": 6,
                    "leader_commit": 30,
                    "entries": [
                        {"index": 31, "term": 7, "op": "set", "key": "n", "value": 4},
                        {"index": 32, "term": 7, "op": "incr", "key": "n", "delta": 2},
                    ],
                })
            ],
            "0002.rlog": [
                rec({
                    "node": "h",
                    "prev_index": 32,
                    "prev_term": 7,
                    "leader_commit": 32,
                    "entries": [],
                })
            ],
            "0004.rlog": [
                rec({
                    "node": "h",
                    "prev_index": 32,
                    "prev_term": 7,
                    "leader_commit": 34,
                    "entries": [
                        {"index": 33, "term": 8, "op": "batch", "ops": [
                            {"op": "cas", "key": "missing", "expect": 0, "value": 12},
                            {"op": "del", "key": "keep"},
                        ]},
                        {"index": 34, "term": 8, "op": "incr", "key": "missing", "delta": -2},
                    ],
                }),
                "{not json",
                rec({
                    "node": "h",
                    "prev_index": 34,
                    "prev_term": 8,
                    "leader_commit": 35,
                    "entries": [{"index": 35, "term": 8, "op": "set", "key": "after_bad", "value": 1}],
                }),
            ],
            "0005.rlog": [
                rec({
                    "node": "h",
                    "prev_index": 34,
                    "prev_term": 8,
                    "leader_commit": 35,
                    "entries": [{"index": 35, "term": 9, "op": "set", "key": "done", "value": 1}],
                })
            ],
        },
    )
    incomplete = rec({
        "node": "h",
        "prev_index": 32,
        "prev_term": 7,
        "leader_commit": 33,
        "entries": [{"index": 33, "term": 7, "op": "set", "key": "n", "value": 99}],
    })
    (case_dir / "segments" / "0003.rlog").write_text(incomplete)
    actual = run_case(case_dir, tmp_path / "out-four")
    expected = reference(case_dir)
    # Verifies empty-entry commit advancement, malformed JSON aborts, incomplete-tail aborts, and absent-key cas as 0.
    assert_state_matches(actual, expected)


def test_generated_case_set_five(tmp_path):
    """Rejected predecessors, blank lines, duplicates, nested ops, and abort counts interact."""
    build_binary()
    case_dir = tmp_path / "case-five"
    nested_73 = {
        "index": 73,
        "term": 7,
        "op": "batch",
        "ops": [
            {"op": "cas", "key": "new", "expect": 3, "value": 30},
            {"op": "incr", "key": "new", "delta": 2},
            {"op": "del", "key": "stale"},
        ],
    }
    ignored_after_crc = {
        "node": "matrix",
        "prev_index": 74,
        "prev_term": 7,
        "leader_commit": 75,
        "entries": [{"index": 75, "term": 7, "op": "set", "key": "should_not_apply", "value": 1}],
    }
    write_case(
        case_dir,
        {
            "mid.json": {"id": "candidate-z", "index": 70, "term": 5, "kv": {"root": 99}},
            "newest.json": {"id": "candidate-a", "index": 70, "term": 5, "kv": {"root": 10, "stale": 8}},
            "older.json": {"id": "candidate-b", "index": 69, "term": 6, "kv": {"root": 200}},
        },
        {
            "0001.rlog": [
                rec({
                    "node": "matrix",
                    "prev_index": 69,
                    "prev_term": 5,
                    "leader_commit": 71,
                    "entries": [{"index": 71, "term": 6, "op": "set", "key": "root", "value": 999}],
                }),
                "",
                rec({
                    "node": "matrix",
                    "prev_index": 70,
                    "prev_term": 5,
                    "leader_commit": 70,
                    "entries": [
                        {"index": 71, "term": 6, "op": "set", "key": "root", "value": 11},
                        {"index": 72, "term": 6, "op": "batch", "ops": [
                            {"op": "cas", "key": "new", "expect": 0, "value": 3},
                            {"op": "incr", "key": "root", "delta": 4},
                            {"op": "cas", "key": "stale", "expect": 8, "value": 9},
                        ]},
                    ],
                }),
            ],
            "0002.rlog": [
                rec({
                    "node": "matrix",
                    "prev_index": 72,
                    "prev_term": 6,
                    "leader_commit": 72,
                    "entries": [],
                }),
                rec({
                    "node": "matrix",
                    "prev_index": 72,
                    "prev_term": 6,
                    "leader_commit": 74,
                    "entries": [
                        nested_73,
                        {"index": 74, "term": 7, "op": "incr", "key": "root", "delta": -5},
                    ],
                }),
            ],
            "0003.rlog": [
                rec({
                    "node": "matrix",
                    "prev_index": 72,
                    "prev_term": 6,
                    "leader_commit": 73,
                    "entries": [nested_73],
                }),
                rec(ignored_after_crc, crc="ffffffff"),
                rec(ignored_after_crc),
            ],
        },
    )
    incomplete = rec({
        "node": "matrix",
        "prev_index": 74,
        "prev_term": 7,
        "leader_commit": 75,
        "entries": [{"index": 75, "term": 7, "op": "set", "key": "tail", "value": 1}],
    })
    (case_dir / "segments" / "0004.rlog").write_text(incomplete)
    actual = run_case(case_dir, tmp_path / "out-five")
    expected = reference(case_dir)
    # Verifies predecessor rejection without segment abort, snapshot id tie-breaking, duplicate replay stability,
    # missing-key cas inside a nested batch, heartbeat commits, blank-line skipping, and ignored-line totals.
    assert_state_matches(actual, expected)
