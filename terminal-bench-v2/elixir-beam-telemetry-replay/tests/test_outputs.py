import hashlib
import json
import subprocess
from pathlib import Path


APP = Path("/app/beamtrail")
BIN = APP / "beam_audit"


def run_case(tmp_path, layout):
    case_dir = tmp_path / "case"
    out_dir = tmp_path / "out"
    case_dir.mkdir()
    for rel, content in layout.items():
        path = case_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".json"):
            path.write_text(json.dumps(content, sort_keys=True, separators=(",", ":")) + "\n")
        else:
            path.write_text("".join("\n" if row is None else json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in content))
    before = tree_digest(case_dir)
    subprocess.run(["mix", "escript.build"], cwd=APP, check=True, text=True, capture_output=True)
    subprocess.run([str(BIN), "--input", str(case_dir), "--output", str(out_dir)], cwd=APP, check=True, text=True, capture_output=True)
    after = tree_digest(case_dir)
    assert before == after
    assert sorted(p.name for p in out_dir.iterdir()) == ["summary.json", "timeline.jsonl"]
    summary = json.loads((out_dir / "summary.json").read_text())
    timeline = [json.loads(line) for line in (out_dir / "timeline.jsonl").read_text().splitlines() if line]
    return summary, timeline


def tree_digest(root):
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def norm_ts(rec):
    ticks = rec.get("epoch", 0) * (2**32) + rec.get("mono", rec.get("ts", 0))
    unit = rec.get("unit", "us")
    if unit == "ns":
        return ticks // 1000
    if unit == "ms":
        return ticks * 1000
    return ticks


def load_layout(layout):
    rows = []
    for rel, content in layout.items():
        if not rel.endswith(".jsonl"):
            continue
        for index, row in enumerate(content, 1):
            if row is None:
                continue
            rec = dict(row)
            rec["__file"] = Path(rel).name
            rec["__line"] = index
            rows.append(rec)
    return sorted(rows, key=lambda r: (norm_ts(r), r.get("seq", 0), r["__file"], r["__line"]))


def ensure(state, trace, sup, child_id, ts):
    trace_state = state.setdefault(trace, {"supervisors": {}, "totals": {"events": 0, "duplicates": 0}})
    sup_state = trace_state["supervisors"].setdefault(sup, {"children": {}, "seen": set()})
    sup_state["children"].setdefault(child_id, {
        "current_pid": None,
        "status": "unknown",
        "crashes": 0,
        "restarts": 0,
        "intensity_breached": False,
        "first_ts": ts,
        "last_ts": ts,
        "window": [],
    })
    return trace_state, sup_state, sup_state["children"][child_id]


def apply_snapshot(state, rec):
    trace = rec.get("trace", "default")
    ts = norm_ts(rec)
    trace_state = state.setdefault(trace, {"supervisors": {}, "totals": {"events": 0, "duplicates": 0}})
    for sup in rec.get("state", {}).get("supervisors", []):
        sup_state = trace_state["supervisors"].setdefault(sup["name"], {"children": {}, "seen": set()})
        for item in sup.get("children", []):
            sup_state["children"][item["id"]] = {
                "current_pid": item.get("current_pid", item.get("pid")),
                "status": item.get("status", "unknown"),
                "crashes": item.get("crashes", 0),
                "restarts": item.get("restarts", 0),
                "intensity_breached": item.get("intensity_breached", False),
                "first_ts": item.get("first_ts", ts),
                "last_ts": item.get("last_ts", ts),
                "window": list(item.get("window", [])),
            }


def reference(layout):
    config = layout.get("config.json", {})
    intensity = config.get("restart_intensity", 3)
    period = config.get("restart_period_us", 5_000_000)
    state = {}
    timeline = []
    for rec in load_layout(layout):
        trace = rec.get("trace", "default")
        ts = norm_ts(rec)
        if rec.get("event") == "snapshot":
            apply_snapshot(state, rec)
            state.setdefault(trace, {"supervisors": {}, "totals": {"events": 0, "duplicates": 0}})["totals"]["events"] += 1
            continue
        sup = rec.get("sup", "root")
        child_id = rec.get("id", rec.get("child", "unknown"))
        trace_state, sup_state, child = ensure(state, trace, sup, child_id, ts)
        ref = rec.get("ref")
        key = ref if isinstance(ref, str) and ref else None
        if key and key in sup_state["seen"]:
            trace_state["totals"]["duplicates"] += 1
            continue
        if key:
            sup_state["seen"].add(key)
        trace_state["totals"]["events"] += 1
        child["last_ts"] = ts
        event = rec.get("event")
        if event == "child_start":
            child["current_pid"] = rec.get("pid")
            child["status"] = "running"
        elif event == "child_restart":
            child["current_pid"] = rec.get("pid")
            child["status"] = "running"
            child["restarts"] += 1
        elif event == "child_exit":
            child["current_pid"] = rec.get("pid")
            if rec.get("reason", "shutdown") == "normal":
                child["status"] = "stopped"
            else:
                child["status"] = "crashed"
                child["crashes"] += 1
                child["window"] = [x for x in child["window"] + [ts] if ts - x <= period]
                child["intensity_breached"] = child["intensity_breached"] or len(child["window"]) > intensity
        timeline.append({"trace": trace, "ts": ts, "seq": rec.get("seq", 0), "sup": sup, "id": child_id, "pid": child["current_pid"], "event": event, "status": child["status"]})
    return summarize(state), timeline


def summarize(state):
    traces = []
    for trace, trace_state in sorted(state.items()):
        supervisors = []
        for name, sup_state in sorted(trace_state["supervisors"].items()):
            children = []
            for child_id, child in sorted(sup_state["children"].items()):
                children.append({"id": child_id, **{k: child[k] for k in ["current_pid", "status", "crashes", "restarts", "intensity_breached", "first_ts", "last_ts"]}})
            supervisors.append({"name": name, "children": children})
        traces.append({"trace": trace, "totals": trace_state["totals"], "supervisors": supervisors})
    return {"generated_by": "beam_audit", "traces": traces}


def assert_case(tmp_path, layout):
    got_summary, got_timeline = run_case(tmp_path, layout)
    exp_summary, exp_timeline = reference(layout)
    assert got_summary == exp_summary
    assert got_timeline == exp_timeline


def test_reconstructs_case_alpha(tmp_path):
    """The audit output matches an independent replay for a generated event set."""
    layout = {
        "config.json": {"restart_intensity": 3, "restart_period_us": 5000},
        "b/part-b.jsonl": [
            {"trace": "alpha", "seq": 50, "mono": 1, "unit": "ms", "event": "child_start", "sup": "Root", "id": "cache", "pid": "<0.10.0>", "ref": "a-start"},
            {"trace": "alpha", "seq": 70, "mono": 2200, "unit": "us", "event": "child_restart", "sup": "Root", "id": "cache", "pid": "<0.11.0>", "ref": "a-restart"},
        ],
        "a/part-a.jsonl": [
            {"trace": "alpha", "seq": 10, "mono": 1_500_000, "unit": "ns", "event": "child_exit", "sup": "Root", "id": "cache", "pid": "<0.10.0>", "reason": "error", "ref": "a-exit-1"},
            {"trace": "alpha", "seq": 11, "mono": 1_600_000, "unit": "ns", "event": "child_exit", "sup": "Root", "id": "cache", "pid": "<0.10.0>", "reason": "error", "ref": "a-exit-2"},
            {"trace": "alpha", "seq": 12, "mono": 1_600_000, "unit": "ns", "event": "child_exit", "sup": "Root", "id": "cache", "pid": "<0.10.0>", "reason": "error", "ref": "a-exit-2"},
        ],
    }
    assert_case(tmp_path, layout)


def test_reconstructs_case_beta(tmp_path):
    """The audit output remains stable across a compacted generated event set."""
    layout = {
        "config.json": {"restart_intensity": 2, "restart_period_us": 3000},
        "main.jsonl": [
            {"trace": "beta", "seq": 1, "mono": 4_294_967_290, "unit": "us", "epoch": 0, "event": "snapshot", "state": {"supervisors": [{"name": "Root", "children": [{"id": "api", "current_pid": "<0.20.0>", "status": "running", "crashes": 1, "restarts": 1, "first_ts": 900, "last_ts": 1200, "window": [1200]}]}]}},
            {"trace": "beta", "seq": 2, "mono": 2, "unit": "us", "epoch": 1, "event": "child_exit", "sup": "Root", "id": "api", "pid": "<0.20.0>", "reason": "error", "ref": "b-exit"},
            {"trace": "beta", "seq": 3, "mono": 3, "unit": "us", "epoch": 1, "event": "child_restart", "sup": "Root", "id": "api", "pid": "<0.21.0>", "ref": "b-restart"},
            {"trace": "beta", "seq": 4, "mono": 4, "unit": "us", "epoch": 1, "event": "child_exit", "sup": "Root", "id": "api", "pid": "<0.21.0>", "reason": "normal", "ref": "b-stop"},
        ],
    }
    assert_case(tmp_path, layout)


def test_reconstructs_case_gamma(tmp_path):
    """The audit output is data-driven for another generated trace."""
    layout = {
        "config.json": {"restart_intensity": 2, "restart_period_us": 1_000_000},
        "one.jsonl": [
            {"trace": "gamma", "seq": 3, "mono": 100, "unit": "us", "event": "child_start", "sup": "Aux", "id": "meter", "pid": "<0.30.0>", "ref": "g-start"},
            None,
            {"trace": "gamma", "seq": 4, "mono": 1_000_500, "unit": "us", "event": "child_exit", "sup": "Aux", "id": "meter", "pid": "<0.30.0>", "reason": "error", "ref": "g-exit-1"},
            {"trace": "gamma", "seq": 5, "mono": 1_000_600, "unit": "us", "event": "child_restart", "sup": "Aux", "id": "meter", "pid": "<0.31.0>", "ref": "g-r1"},
        ],
        "two.jsonl": [
            {"trace": "gamma", "seq": 6, "mono": 1_001_000, "unit": "us", "event": "child_exit", "sup": "Aux", "id": "meter", "pid": "<0.31.0>", "reason": "error", "ref": "g-exit-2"},
            {"trace": "gamma", "seq": 7, "mono": 1_001_100, "unit": "us", "event": "child_restart", "sup": "Aux", "id": "meter", "pid": "<0.32.0>", "ref": "g-r2"},
            {"trace": "gamma", "seq": 8, "mono": 1_001_200, "unit": "us", "event": "child_exit", "sup": "Aux", "id": "meter", "pid": "<0.32.0>", "reason": "error", "ref": "g-exit-3"},
        ],
    }
    assert_case(tmp_path, layout)
