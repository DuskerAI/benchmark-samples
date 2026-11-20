import json
import subprocess
from pathlib import Path


ROOT = Path("/app/wasmguard")
BIN = ROOT / "bin" / "wasmguard"


def leb(n):
    out = []
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def sec(i, payload):
    return bytes([i]) + leb(len(payload)) + payload


def module(sections):
    return b"WSMG" + bytes([1]) + b"".join(sections)


def imp(*names):
    payload = leb(len(names))
    for name in names:
        raw = name.encode()
        payload += leb(len(raw)) + raw
    return sec(1, payload)


def limits(i, a, b):
    return sec(i, leb(a) + leb(b))


def overflow_limits(i):
    return sec(i, leb(1) + b"\x80\x80\x80\x80\x10")


def code(raw):
    return sec(4, raw)


def run_case(tmp_path, entries):
    d = tmp_path / "in"
    d.mkdir()
    for rel, blob in entries.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    report = tmp_path / "report.json"
    subprocess.run([str(BIN), "--input", str(d), "--report", str(report)], check=True)
    return json.loads(report.read_text())


def by_path(report):
    return {m["path"]: m for m in report["modules"]}


def test_report_counts_and_acceptance(tmp_path):
    """The auditor emits deterministic module records and accepts valid local capability use."""
    good = module([
        imp("env.clock", "env.memory", "env.table"),
        limits(2, 1, 4),
        limits(3, 0, 4),
        code(b"\x02\x03\x0c\x00\x0b\x0b"),
    ])
    bad = module([imp("net.fetch"), limits(2, 1, 2), limits(3, 0, 1), code(b"\x0b")])
    report = run_case(tmp_path, {"z/reject.wsm": bad, "a/accept.wsm": good})
    rows = report["modules"]
    assert set(report) == {"modules", "accepted", "rejected"}
    assert set(rows[0]) == {"path", "status", "reason", "limits"}
    assert set(rows[0]["limits"]) == {"memory_min", "memory_max", "table_min", "table_max"}
    assert [r["path"] for r in rows] == ["a/accept.wsm", "z/reject.wsm"]
    assert report["accepted"] == 1
    assert report["rejected"] == 1
    acc = by_path(report)["a/accept.wsm"]
    assert acc["reason"] == "ok"
    assert acc["limits"]["memory_min"] == 1
    assert acc["limits"]["memory_max"] == 4
    assert acc["limits"]["table_min"] == 0
    assert acc["limits"]["table_max"] == 4
    assert by_path(report)["z/reject.wsm"]["reason"] == "import-policy"


def test_policy_order_and_bounds(tmp_path):
    """Admission reasons follow the public precedence and include reported limits."""
    dup = module([
        sec(5, b"x"),
        sec(5, b"y"),
        imp("bad.name"),
        overflow_limits(2),
        limits(3, 5, 1),
        code(b"\x0c\x00"),
    ])
    both = imp("env.memory", "env.table")
    mem = module([both, limits(2, 0, 16), limits(3, 1, 128), code(b"\x0b")])
    memhi = module([both, limits(2, 1, 17), limits(3, 0, 128), code(b"\x02\x0b")])
    table = module([both, limits(2, 1, 16), limits(3, 128, 129), code(b"\x0b")])
    report = by_path(run_case(tmp_path, {"dup.wsm": dup, "mem.wsm": mem, "memhi.wsm": memhi, "tbl.wsm": table}))
    assert report["dup.wsm"]["reason"] == "duplicate-section"
    assert report["mem.wsm"]["reason"] == "memory-policy"
    assert report["mem.wsm"]["limits"]["memory_min"] == 0
    assert report["memhi.wsm"]["reason"] == "memory-policy"
    assert report["tbl.wsm"]["reason"] == "table-policy"


def test_absent_resource_sections_report_null_limits(tmp_path):
    """Accepted modules without memory or table sections report null resource limits."""
    minimal = module([imp("env.clock"), code(b"\x02\x0b")])
    report = by_path(run_case(tmp_path, {"minimal.wsm": minimal}))
    assert report["minimal.wsm"]["status"] == "accepted"
    assert report["minimal.wsm"]["reason"] == "ok"
    assert report["minimal.wsm"]["limits"] == {
        "memory_min": None,
        "memory_max": None,
        "table_min": None,
        "table_max": None,
    }


def test_nested_control_and_large_encodings(tmp_path):
    """Nested labels, branch depths, and multibyte lengths are checked together."""
    pad = sec(0, bytes(range(160)))
    caps = imp("env.memory", "env.table")
    good = module([pad, caps, limits(2, 1, 16), limits(3, 0, 128), code(b"\x02\x02\x0c" + leb(1) + b"\x0b\x0b")])
    target = module([caps, limits(2, 1, 16), limits(3, 0, 128), code(b"\x02\x0c" + leb(1) + b"\x0b")])
    trailing = b"WSMG\x01" + bytes([0]) + b"\x81"
    overflow = module([overflow_limits(2), limits(3, 0, 128), code(b"\x02\x0b")])
    report = by_path(run_case(
        tmp_path,
        {"good.wsm": good, "ctrl.wsm": target, "trunc.wsm": trailing, "overflow.wsm": overflow},
    ))
    assert report["good.wsm"]["status"] == "accepted"
    assert report["ctrl.wsm"]["reason"] == "control-policy"
    assert report["trunc.wsm"]["reason"] == "malformed"
    assert report["overflow.wsm"]["reason"] == "malformed"


def test_malformed_leb_and_limits_are_classified_before_policies(tmp_path):
    """Minimal LEB128 encodings and complete resource limits are enforced as structure."""
    overlong = module([
        sec(2, b"\x80\x00" + leb(16)),
        limits(3, 0, 128),
        code(b"\x02\x0b"),
    ])
    oneval = module([sec(2, leb(4)), limits(3, 0, 128), code(b"\x02\x0b")])
    report = by_path(run_case(
        tmp_path,
        {"overlong.wsm": overlong, "oneval.wsm": oneval},
    ))
    assert report["overlong.wsm"]["reason"] == "malformed"
    assert report["oneval.wsm"]["reason"] == "malformed"


def test_sorting_uses_relative_paths(tmp_path):
    """Reports use relative paths and sort them lexicographically."""
    ok = module([imp("env.clock"), code(b"\x02\x0b")])
    report = run_case(tmp_path, {"b/first.wsm": ok, "a/second.wsm": ok, "skip.txt": b"not a module"})
    assert [r["path"] for r in report["modules"]] == ["a/second.wsm", "b/first.wsm"]


def test_exact_capabilities_and_closed_labels(tmp_path):
    """Admission checks exact local capabilities and code label structure."""
    cap = module([imp("env.memory.shadow"), limits(2, 1, 16), limits(3, 0, 128), code(b"\x02\x0b")])
    flow = module([imp("env.memory", "env.table"), limits(2, 1, 16), limits(3, 0, 128), code(b"\x0b")])
    ok = module([imp("env.memory", "env.table"), limits(2, 1, 1), limits(3, 0, 0), code(b"\x02\x0b")])
    report = by_path(run_case(tmp_path, {"cap.wsm": cap, "flow.wsm": flow, "ok.wsm": ok}))
    assert report["cap.wsm"]["reason"] == "import-policy"
    assert report["flow.wsm"]["reason"] == "control-policy"
    assert report["ok.wsm"]["status"] == "accepted"


def test_capability_manifest_consistency(tmp_path):
    """Import vectors are exact, unique, and agree with represented resources."""
    valid = module([imp("env.clock", "env.memory"), limits(2, 2, 7), code(b"\x04\x05\x0b")])
    repeated = module([imp("env.clock", "env.clock"), code(b"\x02\x0b")])
    missing = module([imp("env.clock"), limits(2, 1, 2), code(b"\x02\x0b")])
    phantom = module([imp("env.table"), code(b"\x02\x0b")])
    truncated = module([sec(1, leb(2) + leb(9) + b"env.clock"), code(b"\x02\x0b")])
    invalid_text = module([sec(1, leb(1) + leb(1) + b"\xff"), code(b"\x02\x0b")])
    trailing = module([sec(1, leb(0) + b"x"), code(b"\x02\x0b")])
    long_count = module([sec(1, b"\x80\x00"), code(b"\x02\x0b")])
    long_name = module([sec(1, leb(1) + b"\x89\x00" + b"env.clock"), code(b"\x02\x0b")])
    rows = by_path(run_case(tmp_path, {
        "valid.wsm": valid,
        "repeated.wsm": repeated,
        "missing.wsm": missing,
        "phantom.wsm": phantom,
        "truncated.wsm": truncated,
        "text.wsm": invalid_text,
        "trailing.wsm": trailing,
        "count.wsm": long_count,
        "length.wsm": long_name,
    }))
    assert rows["valid.wsm"]["status"] == "accepted"
    for name in ("repeated.wsm", "missing.wsm", "phantom.wsm"):
        assert rows[name]["reason"] == "import-policy"
    for name in ("truncated.wsm", "text.wsm", "trailing.wsm", "count.wsm", "length.wsm"):
        assert rows[name]["reason"] == "malformed"


def test_structural_classification_and_precedence(tmp_path):
    """Structural validity and duplicate-section precedence remain stable across policies."""
    bad_branch = module([imp("bad.name"), code(b"\x02\x0c\x80\x00\x0b")])
    bad_table = module([imp("bad.name"), code(b"\x02\x0e\x03\x00\x0b")])
    repeated = module([sec(7, b"a"), sec(7, b"b"), code(b"\x0c\x80\x00")])
    framing = b"WSMG\x01" + bytes([6]) + b"\x80\x00"
    custom = module([sec(0, b"alpha"), sec(0, b"beta"), imp("env.clock"), code(b"\x02\x0b")])
    header = b"WSMG\x02"
    wide = module([sec(3, leb(0) + leb(1) + leb(2)), imp("env.table"), code(b"\x02\x0b")])
    rows = by_path(run_case(tmp_path, {
        "branch.wsm": bad_branch,
        "table.wsm": bad_table,
        "repeat.wsm": repeated,
        "frame.wsm": framing,
        "custom.wsm": custom,
        "header.wsm": header,
        "wide.wsm": wide,
    }))
    assert rows["branch.wsm"]["reason"] == "malformed"
    assert rows["table.wsm"]["reason"] == "malformed"
    assert rows["repeat.wsm"]["reason"] == "duplicate-section"
    assert rows["frame.wsm"]["reason"] == "malformed"
    assert rows["custom.wsm"]["status"] == "accepted"
    assert rows["header.wsm"]["reason"] == "malformed"
    assert rows["wide.wsm"]["reason"] == "malformed"


def test_control_stack_semantics(tmp_path):
    """Conditional labels and branch vectors obey the complete control-stack policy."""
    nested = b"\x02\x04\x0d" + leb(1) + b"\x05\x0e" + leb(2) + leb(0) + leb(1) + leb(0) + b"\x0b\x0b"
    deep = b"\x02" * 129 + b"\x0c" + leb(128) + b"\x0b" * 129
    duplicate_else = b"\x04\x05\x05\x0b"
    misplaced_else = b"\x04\x02\x05\x0b\x0b"
    bad_default = b"\x02\x0e" + leb(1) + leb(0) + leb(1) + b"\x0b"
    ordinary = b"\xff\x02\x7f\x0b"
    rows = by_path(run_case(tmp_path, {
        "nested.wsm": module([code(nested)]),
        "deep.wsm": module([code(deep)]),
        "else1.wsm": module([code(duplicate_else)]),
        "else2.wsm": module([code(misplaced_else)]),
        "default.wsm": module([code(bad_default)]),
        "ordinary.wsm": module([code(ordinary)]),
    }))
    assert rows["nested.wsm"]["status"] == "accepted"
    assert rows["deep.wsm"]["status"] == "accepted"
    assert rows["ordinary.wsm"]["status"] == "accepted"
    for name in ("else1.wsm", "else2.wsm", "default.wsm"):
        assert rows[name]["reason"] == "control-policy"


def test_generated_resource_boundaries(tmp_path):
    """Resource policies hold across a deterministic matrix of boundary combinations."""
    entries = {}
    expected = {}
    memory_pairs = [(1, 1), (1, 16), (16, 16), (0, 1), (1, 17), (8, 7)]
    table_pairs = [(0, 0), (0, 128), (128, 128), (0, 129), (9, 8)]
    for index, pair in enumerate(memory_pairs):
        name = f"m/{index}.wsm"
        entries[name] = module([imp("env.memory"), limits(2, *pair), code(b"\x02\x0b")])
        expected[name] = "ok" if 1 <= pair[0] <= pair[1] <= 16 else "memory-policy"
    for index, pair in enumerate(table_pairs):
        name = f"t/{index}.wsm"
        entries[name] = module([imp("env.table"), limits(3, *pair), code(b"\x02\x0b")])
        expected[name] = "ok" if 0 <= pair[0] <= pair[1] <= 128 else "table-policy"
    rows = by_path(run_case(tmp_path, entries))
    assert {name: row["reason"] for name, row in rows.items()} == expected
