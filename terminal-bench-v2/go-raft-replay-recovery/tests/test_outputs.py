"""Behavioral tests for the Raft trace report CLI."""

import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path


APP = Path("/app/task_file")


def _majority(n):
    return n // 2 + 1


def _norm_membership(membership):
    return {
        "phase": membership.get("phase", ""),
        "voters": sorted(membership.get("voters", [])),
        "learners": sorted(membership.get("learners", [])),
        **(
            {"outgoing_voters": sorted(membership.get("outgoing_voters", []))}
            if membership.get("outgoing_voters")
            else {}
        ),
    }


def _copy_sessions(sessions):
    return {k: dict(v) for k, v in sessions.items()}


def _voter_quorum(membership, holders):
    voters = membership.get("voters", [])
    outgoing = membership.get("outgoing_voters", [])
    if outgoing:
        return (
            sum(1 for node in outgoing if node in holders) >= _majority(len(outgoing))
            and sum(1 for node in voters if node in holders) >= _majority(len(voters))
        )
    return sum(1 for node in voters if node in holders) >= _majority(len(voters))


def _entry_fingerprint(entry):
    normalized = deepcopy(entry)
    normalized["membership"] = _norm_membership(normalized.get("membership", {}))
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _replay_trace(path):
    trace = json.loads(Path(path).read_text())
    nodes = {}
    kv = {}
    sessions = {}
    membership = {"phase": "stable", "voters": [], "learners": []}
    counters = {
        "commands": 0,
        "configs": 0,
        "snapshots": 0,
        "duplicates": 0,
        "ignored_entries": 0,
    }
    committed = 0
    last_term = 0

    def node(name):
        return nodes.setdefault(name, {"log": {}, "snap": None, "leader_commit": 0})

    def term_at(state, index):
        snap = state["snap"]
        if index == 0 and not snap:
            return 0, True
        if snap and index == snap["index"]:
            return snap["term"], True
        if snap and index < snap["index"]:
            return None, False
        ent = state["log"].get(index)
        return (ent.get("term"), True) if ent else (None, False)

    def pick_snapshot(live):
        best = None
        for state in nodes.values():
            snap = state.get("snap")
            if not snap:
                continue
            holders = set()
            for name, other in nodes.items():
                osnap = other.get("snap")
                same_snapshot = json.dumps(osnap, sort_keys=True) == json.dumps(
                    snap,
                    sort_keys=True,
                )
                if name in live and osnap and same_snapshot:
                    holders.add(name)
            newer = best is None or snap["index"] > best["index"]
            if _voter_quorum(snap["membership"], holders) and newer:
                best = snap
        return best

    def choose_entry(index, live, current_membership):
        by_term = {}
        holders_by_term = {}
        for name, state in nodes.items():
            if name not in live:
                continue
            ent = state["log"].get(index)
            if not ent:
                continue
            fingerprint = _entry_fingerprint(ent)
            by_term.setdefault(ent["term"], {})[fingerprint] = ent
            holders_by_term.setdefault(ent["term"], {}).setdefault(fingerprint, set()).add(name)
        for term in sorted(by_term, reverse=True):
            for fingerprint in sorted(by_term[term]):
                holders = holders_by_term[term][fingerprint]
                ent = by_term[term][fingerprint]
                if not _voter_quorum(current_membership, holders):
                    continue
                incoming = []
                if ent.get("type") == "config":
                    incoming = ent.get("membership", {}).get("voters", [])
                if ent.get("type") == "config" and ent.get("membership", {}).get("phase") == "joint":
                    incoming_count = sum(1 for node_name in incoming if node_name in holders)
                    if incoming_count < _majority(len(incoming)):
                        continue
                return ent
        return None

    def apply_entry(ent):
        nonlocal membership
        if ent["type"] == "config":
            membership = _norm_membership(ent["membership"])
            counters["configs"] += 1
            return
        if ent["type"] != "command":
            return
        previous = sessions.get(ent.get("client_id"))
        if previous and ent.get("seq", 0) <= previous.get("seq", 0):
            counters["duplicates"] += 1
            return
        cmd = ent["command"]
        if cmd["op"] == "set":
            kv[cmd["key"]] = cmd["value"]
            response = cmd["value"]
        elif cmd["op"] == "incr":
            kv[cmd["key"]] = kv.get(cmd["key"], 0) + cmd.get("delta", 0)
            response = kv[cmd["key"]]
        elif cmd["op"] == "delete":
            response = kv.get(cmd["key"], 0)
            kv.pop(cmd["key"], None)
        elif cmd["op"] == "cas":
            if kv.get(cmd["key"], 0) == cmd.get("expect", 0):
                kv[cmd["key"]] = cmd["value"]
                response = 1
            else:
                response = 0
        else:
            response = 0
        sessions[ent["client_id"]] = {
            "seq": ent["seq"],
            "response": response,
            "request_id": ent.get("request_id", ""),
        }
        counters["commands"] += 1

    for event in trace["events"]:
        kind = event["type"]
        if kind == "cluster":
            membership = _norm_membership(event["membership"])
        elif kind == "install_snapshot":
            state = node(event["node"])
            current = state.get("snap")
            if current and event["last_index"] <= current["index"]:
                continue
            state["snap"] = {
                "index": event["last_index"],
                "term": event["last_term"],
                "kv": dict(event.get("state", {})),
                "sessions": _copy_sessions(event.get("sessions", {})),
                "membership": _norm_membership(event["membership"]),
            }
            state["log"] = {
                int(k): v
                for k, v in state["log"].items()
                if int(k) > event["last_index"]
            }
            counters["snapshots"] += 1
        elif kind == "append_entries":
            state = node(event["node"])
            term, ok = term_at(state, event.get("prev_index", 0))
            if not ok or (term is not None and term != event.get("prev_term", 0)):
                counters["ignored_entries"] += len(event.get("entries", []))
                continue
            for ent in event.get("entries", []):
                ent = deepcopy(ent)
                ent["membership"] = _norm_membership(ent.get("membership", {}))
                snap = state.get("snap")
                if snap and ent["index"] <= snap["index"]:
                    counters["ignored_entries"] += 1
                    continue
                old = state["log"].get(ent["index"])
                if old and old["term"] != ent["term"]:
                    state["log"] = {i: e for i, e in state["log"].items() if i < ent["index"]}
                state["log"][ent["index"]] = ent
            state["leader_commit"] = max(state["leader_commit"], event.get("leader_commit", 0))
        elif kind == "crash_cut":
            live = set(event.get("live") or nodes.keys())
            snap = pick_snapshot(live)
            if snap and snap["index"] > committed:
                committed = snap["index"]
                last_term = snap["term"]
                kv.clear()
                kv.update(snap["kv"])
                sessions.clear()
                sessions.update(_copy_sessions(snap["sessions"]))
                membership = _norm_membership(snap["membership"])
            bound = max(
                (nodes[name]["leader_commit"] for name in live if name in nodes),
                default=0,
            )
            while committed < bound:
                ent = choose_entry(committed + 1, live, membership)
                if not ent:
                    break
                apply_entry(ent)
                committed += 1
                last_term = ent["term"]

    return {
        "name": Path(path).name,
        "committed_index": committed,
        "last_term": last_term,
        "kv": kv,
        "sessions": sessions,
        "membership": _norm_membership(membership),
        "counters": counters,
    }


def _expected_for_dir(input_dir):
    traces = [_replay_trace(path) for path in sorted(Path(input_dir).glob("*.json"))]
    return {
        "traces": traces,
        "totals": {
            "traces": len(traces),
            "committed_entries": sum(
                t["counters"]["commands"] + t["counters"]["configs"]
                for t in traces
            ),
            "snapshots": sum(t["counters"]["snapshots"] for t in traces),
            "duplicates": sum(t["counters"]["duplicates"] for t in traces),
        },
    }


def _run_tool(input_dir, output_path):
    result = subprocess.run(
        [
            "go",
            "run",
            "./cmd/raftreport",
            "--input",
            str(input_dir),
            "--output",
            str(output_path),
        ],
        cwd=APP,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(Path(output_path).read_text())


def _command(index, term, seq, request_id, command):
    return {
        "index": index,
        "term": term,
        "type": "command",
        "client_id": "c",
        "seq": seq,
        "request_id": request_id,
        "command": command,
    }


def _config(index):
    return {
        "index": index,
        "term": 2,
        "type": "config",
        "membership": {
            "phase": "stable",
            "voters": ["r1", "r2", "r5"],
            "learners": ["r4"],
        },
    }


def _append(node, prev_index, prev_term, leader_commit, entries):
    return {
        "type": "append_entries",
        "node": node,
        "prev_index": prev_index,
        "prev_term": prev_term,
        "leader_commit": leader_commit,
        "entries": entries,
    }


def test_bundled_traces_match_reference_model(tmp_path):
    """Bundled traces cover joint quorums, snapshots, prefix repair, and deduplication."""
    output = tmp_path / "report.json"
    actual = _run_tool(APP / "traces", output)
    expected = _expected_for_dir(APP / "traces")
    assert actual == expected


def test_output_is_stable_and_schema_is_complete(tmp_path):
    """Repeated bundled runs should keep lexical trace order and the exact JSON schema."""
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _run_tool(APP / "traces", first)
    _run_tool(APP / "traces", second)
    assert first.read_bytes() == second.read_bytes()
    report = json.loads(first.read_text())
    assert [trace["name"] for trace in report["traces"]] == sorted(
        trace["name"] for trace in report["traces"]
    )
    for trace in report["traces"]:
        assert set(trace) == {
            "name",
            "committed_index",
            "last_term",
            "kv",
            "sessions",
            "membership",
            "counters",
        }
        assert set(trace["counters"]) == {
            "commands",
            "configs",
            "snapshots",
            "duplicates",
            "ignored_entries",
        }


def test_generated_cut_uses_voting_membership_and_prefix_rules(tmp_path):
    """Generated replay should repair conflicts and ignore learners for voting quorums."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    retry = _command(
        4,
        2,
        2,
        "y-again",
        {"op": "incr", "key": "k", "delta": 50},
    )
    generated = {
        "events": [
            {
                "type": "cluster",
                "membership": {
                    "phase": "stable",
                    "voters": ["r1", "r2", "r3"],
                    "learners": ["r4"],
                },
            },
            _append(
                "r1",
                0,
                0,
                4,
                [
                    _command(1, 1, 1, "x", {"op": "set", "key": "k", "value": 4}),
                    _command(2, 1, 2, "y", {"op": "incr", "key": "k", "delta": 6}),
                    _config(3),
                    retry,
                ],
            ),
            _append(
                "r2",
                0,
                0,
                2,
                [
                    _command(1, 1, 1, "x", {"op": "set", "key": "k", "value": 4}),
                    _command(2, 7, 2, "bad", {"op": "set", "key": "k", "value": 99}),
                ],
            ),
            _append(
                "r2",
                1,
                1,
                4,
                [
                    _command(2, 1, 2, "y", {"op": "incr", "key": "k", "delta": 6}),
                    _config(3),
                    retry,
                ],
            ),
            _append(
                "r4",
                0,
                0,
                4,
                [
                    _command(1, 1, 1, "x", {"op": "set", "key": "k", "value": 4}),
                    _command(2, 1, 2, "y", {"op": "incr", "key": "k", "delta": 6}),
                    _config(3),
                    retry,
                ],
            ),
            {"type": "crash_cut", "live": ["r1", "r2", "r4", "r5"]},
        ]
    }
    (input_dir / "case.json").write_text(json.dumps(generated))
    actual = _run_tool(input_dir, tmp_path / "out.json")
    assert actual == _expected_for_dir(input_dir)


def test_generated_rejected_append_only_counts_ignored_entries(tmp_path):
    """A bad prev-index/prev-term batch should be rejected without changing the log."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    good = _command(1, 1, 1, "ok", {"op": "set", "key": "k", "value": 7})
    generated = {
        "events": [
            {
                "type": "cluster",
                "membership": {
                    "phase": "stable",
                    "voters": ["a", "b", "c"],
                    "learners": [],
                },
            },
            _append("a", 0, 0, 1, [good]),
            _append("b", 0, 0, 1, [good]),
            _append(
                "a",
                9,
                9,
                11,
                [
                    _command(10, 4, 2, "bad-a", {"op": "set", "key": "k", "value": 90}),
                    _command(11, 4, 3, "bad-b", {"op": "incr", "key": "k", "delta": 1}),
                ],
            ),
            {"type": "crash_cut", "live": ["a", "b", "c"]},
        ]
    }
    (input_dir / "ignored.json").write_text(json.dumps(generated))
    actual = _run_tool(input_dir, tmp_path / "ignored-out.json")
    assert actual == _expected_for_dir(input_dir)
    trace = actual["traces"][0]
    assert trace["kv"] == {"k": 7}
    assert trace["counters"]["ignored_entries"] == 2


def test_generated_snapshots_are_monotonic_and_identity_quorumed(tmp_path):
    """Generated cuts should not roll back snapshots or mix unequal compacted states."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    cluster = {
        "type": "cluster",
        "membership": {
            "phase": "stable",
            "voters": ["a", "b", "c"],
            "learners": [],
        },
    }
    membership = cluster["membership"]
    monotonic = {
        "events": [
            cluster,
            {
                "type": "install_snapshot",
                "node": "a",
                "last_index": 5,
                "last_term": 2,
                "state": {"x": 8},
                "sessions": {"c": {"seq": 2, "response": 8, "request_id": "s2"}},
                "membership": membership,
            },
            {
                "type": "install_snapshot",
                "node": "b",
                "last_index": 5,
                "last_term": 2,
                "state": {"x": 8},
                "sessions": {"c": {"seq": 2, "response": 8, "request_id": "s2"}},
                "membership": membership,
            },
            {
                "type": "install_snapshot",
                "node": "a",
                "last_index": 3,
                "last_term": 1,
                "state": {"x": 99},
                "sessions": {},
                "membership": membership,
            },
            _append(
                "a",
                5,
                2,
                6,
                [_command(6, 3, 3, "s3", {"op": "incr", "key": "x", "delta": 4})],
            ),
            _append(
                "b",
                5,
                2,
                6,
                [_command(6, 3, 3, "s3", {"op": "incr", "key": "x", "delta": 4})],
            ),
            {"type": "crash_cut", "live": ["a", "b", "c"]},
        ]
    }
    split_snapshots = {
        "events": [
            cluster,
            {
                "type": "install_snapshot",
                "node": "a",
                "last_index": 8,
                "last_term": 3,
                "state": {"x": 1},
                "sessions": {},
                "membership": membership,
            },
            {
                "type": "install_snapshot",
                "node": "b",
                "last_index": 8,
                "last_term": 3,
                "state": {"x": 2},
                "sessions": {},
                "membership": membership,
            },
            {
                "type": "install_snapshot",
                "node": "c",
                "last_index": 8,
                "last_term": 3,
                "state": {"x": 3},
                "sessions": {},
                "membership": membership,
            },
            {"type": "crash_cut", "live": ["a", "b", "c"]},
        ]
    }
    (input_dir / "001-monotonic.json").write_text(json.dumps(monotonic))
    (input_dir / "002-split.json").write_text(json.dumps(split_snapshots))
    actual = _run_tool(input_dir, tmp_path / "snapshots-out.json")
    assert actual == _expected_for_dir(input_dir)
    monotonic_trace, split_trace = actual["traces"]
    assert monotonic_trace["committed_index"] == 6
    assert monotonic_trace["kv"] == {"x": 12}
    assert monotonic_trace["counters"]["snapshots"] == 2
    assert split_trace["committed_index"] == 0
    assert split_trace["kv"] == {}


def test_generated_appends_cannot_extend_from_before_snapshot_boundary(tmp_path):
    """Compacted logs should not accept new suffixes anchored before the snapshot."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    membership = {"phase": "stable", "voters": ["a", "b", "c"], "learners": []}
    snapshot = {
        "last_index": 5,
        "last_term": 2,
        "state": {"x": 11},
        "sessions": {"c": {"seq": 1, "response": 11, "request_id": "s1"}},
        "membership": membership,
    }
    stale_suffix = _command(6, 3, 2, "stale", {"op": "incr", "key": "x", "delta": 100})
    good_suffix = _command(6, 3, 2, "good", {"op": "incr", "key": "x", "delta": 4})
    generated = {
        "events": [
            {"type": "cluster", "membership": membership},
            {"type": "install_snapshot", "node": "a", **snapshot},
            {"type": "install_snapshot", "node": "b", **snapshot},
            _append("a", 4, 2, 6, [stale_suffix]),
            _append("b", 4, 2, 6, [stale_suffix]),
            {"type": "crash_cut", "live": ["a", "b", "c"]},
            _append("a", 5, 2, 6, [good_suffix]),
            _append("b", 5, 2, 6, [good_suffix]),
            {"type": "crash_cut", "live": ["a", "b", "c"]},
        ]
    }
    (input_dir / "snapshot-boundary.json").write_text(json.dumps(generated))
    actual = _run_tool(input_dir, tmp_path / "boundary-out.json")
    assert actual == _expected_for_dir(input_dir)
    trace = actual["traces"][0]
    assert trace["committed_index"] == 6
    assert trace["kv"] == {"x": 15}
    assert trace["counters"]["ignored_entries"] == 2


def test_generated_same_term_entries_need_identical_payload_quorum(tmp_path):
    """A quorum by term alone should not commit conflicting same-term payloads."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    membership = {"phase": "stable", "voters": ["a", "b", "c", "d", "e"], "learners": []}
    prefix = _command(1, 1, 1, "prefix", {"op": "set", "key": "x", "value": 1})
    left = _command(2, 2, 2, "left", {"op": "incr", "key": "x", "delta": 10})
    right = _command(2, 2, 2, "right", {"op": "incr", "key": "x", "delta": 90})
    generated = {
        "events": [
            {"type": "cluster", "membership": membership},
            _append("a", 0, 0, 2, [prefix, left]),
            _append("b", 0, 0, 2, [prefix, left]),
            _append("c", 0, 0, 2, [prefix, right]),
            _append("d", 0, 0, 2, [prefix, right]),
            {"type": "crash_cut", "live": ["a", "b", "c", "d", "e"]},
            _append("e", 0, 0, 2, [prefix, left]),
            {"type": "crash_cut", "live": ["a", "b", "c", "d", "e"]},
        ]
    }
    (input_dir / "same-term-split.json").write_text(json.dumps(generated))
    actual = _run_tool(input_dir, tmp_path / "same-term-out.json")
    assert actual == _expected_for_dir(input_dir)
    trace = actual["traces"][0]
    assert trace["committed_index"] == 2
    assert trace["kv"] == {"x": 11}


def test_generated_report_preserves_final_joint_membership(tmp_path):
    """A cut ending during joint consensus should emit incoming and outgoing voters."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    joint_config = {
        "index": 1,
        "term": 1,
        "type": "config",
        "membership": {
            "phase": "joint",
            "voters": ["r2", "r3", "r4"],
            "outgoing_voters": ["r1", "r2", "r3"],
            "learners": ["r5"],
        },
    }
    generated = {
        "events": [
            {
                "type": "cluster",
                "membership": {
                    "phase": "stable",
                    "voters": ["r1", "r2", "r3"],
                    "learners": ["r4"],
                },
            },
            _append("r1", 0, 0, 1, [joint_config]),
            _append("r2", 0, 0, 1, [joint_config]),
            _append("r3", 0, 0, 1, [joint_config]),
            {"type": "crash_cut", "live": ["r1", "r2", "r3", "r4"]},
        ]
    }
    (input_dir / "joint.json").write_text(json.dumps(generated))
    actual = _run_tool(input_dir, tmp_path / "joint-out.json")
    expected = _expected_for_dir(input_dir)
    assert actual == expected
    membership = actual["traces"][0]["membership"]
    assert membership == {
        "phase": "joint",
        "voters": ["r2", "r3", "r4"],
        "learners": ["r5"],
        "outgoing_voters": ["r1", "r2", "r3"],
    }


def test_generated_cas_dedup_and_joint_quorum_interact(tmp_path):
    """CAS responses, duplicate sessions, and joint quorums should compose."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    cluster = {
        "phase": "stable",
        "voters": ["a", "b", "c"],
        "learners": ["d"],
    }
    joint = {
        "index": 4,
        "term": 2,
        "type": "config",
        "membership": {
            "phase": "joint",
            "voters": ["b", "c", "d"],
            "outgoing_voters": ["a", "b", "c"],
            "learners": [],
        },
    }
    prefix = [
        _command(1, 1, 1, "seed", {"op": "set", "key": "x", "value": 5}),
        _command(
            2,
            1,
            2,
            "cas-ok",
            {"op": "cas", "key": "x", "expect": 5, "value": 9},
        ),
        _command(
            3,
            2,
            2,
            "dup-cas",
            {"op": "cas", "key": "x", "expect": 9, "value": 99},
        ),
        joint,
    ]
    post_joint = _command(
        5,
        3,
        3,
        "cas-fail",
        {"op": "cas", "key": "x", "expect": 5, "value": 40},
    )
    generated = {
        "events": [
            {"type": "cluster", "membership": cluster},
            _append("a", 0, 0, 4, prefix),
            _append("b", 0, 0, 4, prefix),
            _append("c", 0, 0, 4, prefix),
            {"type": "crash_cut", "live": ["a", "b", "c", "d"]},
            _append("b", 4, 2, 5, [post_joint]),
            _append("c", 4, 2, 5, [post_joint]),
            _append("d", 0, 0, 5, [*prefix, post_joint]),
            {"type": "crash_cut", "live": ["b", "c", "d"]},
        ]
    }
    (input_dir / "joint-cas.json").write_text(json.dumps(generated))
    actual = _run_tool(input_dir, tmp_path / "joint-cas-out.json")
    assert actual == _expected_for_dir(input_dir)
    trace = actual["traces"][0]
    assert trace["committed_index"] == 5
    assert trace["kv"] == {"x": 9}
    assert trace["sessions"] == {"c": {"seq": 3, "response": 0, "request_id": "cas-fail"}}
    assert trace["membership"] == {
        "phase": "joint",
        "voters": ["b", "c", "d"],
        "learners": [],
        "outgoing_voters": ["a", "b", "c"],
    }
    assert trace["counters"]["commands"] == 3
    assert trace["counters"]["duplicates"] == 1


def test_cli_writes_requested_output_path(tmp_path):
    """The CLI should respect caller-supplied input and output paths."""
    input_dir = tmp_path / "copy"
    shutil.copytree(APP / "traces", input_dir)
    output = tmp_path / "nested" / "report.json"
    output.parent.mkdir()
    _run_tool(input_dir, output)
    assert output.exists()
    trace_count = len(list(input_dir.glob("*.json")))
    assert json.loads(output.read_text())["totals"]["traces"] == trace_count
