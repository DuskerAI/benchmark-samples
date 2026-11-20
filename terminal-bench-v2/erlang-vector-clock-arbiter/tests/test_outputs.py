import json
import subprocess


def term(event):
    player, millis, clock, action = event
    clock_text = "[" + ",".join(f"{{{p},{v}}}" for p, v in clock.items()) + "]"
    if action[0] == "move":
        action_text = f"{{move,{action[1]},{action[2]}}}"
    else:
        action_text = f"{{timeout,{action[1]}}}"
    return f"{{{player},{millis},{clock_text},{action_text}}}.\n"


def write_events(path, events):
    path.write_text("".join(term(e) for e in events), encoding="utf-8")


def players_in(event):
    player, _millis, clock, action = event
    players = {player, *clock.keys()}
    if action[0] == "timeout":
        players.add(action[1])
    return players


def vc_before(a, b):
    keys = set(a[2]) | set(b[2])
    leq = all(a[2].get(k, 0) <= b[2].get(k, 0) for k in keys)
    lt = any(a[2].get(k, 0) < b[2].get(k, 0) for k in keys)
    return leq and lt


def ordered(events):
    remaining = list(enumerate(events, start=1))
    out = []
    while remaining:
        ready = [item for item in remaining if not any(vc_before(other[1], item[1]) for other in remaining if other != item)]
        chosen = min(ready, key=lambda item: (item[1][1], item[1][0], item[0]))
        out.append(chosen)
        remaining.remove(chosen)
    return out


def reference(events):
    return reference_from_ordered(events, ordered(events))


def reference_ordered_prefix(events, count):
    return reference_from_ordered(events, ordered(events)[:count])


def reference_from_ordered(all_events, ordered_pairs):
    players = sorted(set().union(*(players_in(e) for e in all_events)))
    scores = {p: 0 for p in players}
    last = {p: 0 for p in players}
    zones = {}
    accepted = 0
    rejected = 0
    order = []
    for source_line, event in ordered_pairs:
        player, _millis, clock, action = event
        self_counter = clock.get(player, 0)
        if self_counter != last.get(player, 0) + 1:
            rejected += 1
            continue
        if action[0] == "move":
            _kind, zone, points = action
            old_owner = zones.get(zone)
            scores[player] = scores.get(player, 0) + points
            if old_owner is not None and old_owner != player:
                scores[old_owner] = scores.get(old_owner, 0) - 1
            zones[zone] = player
        else:
            victim = action[1]
            if clock.get(victim, 0) != last.get(victim, 0):
                rejected += 1
                continue
            scores[victim] = scores.get(victim, 0) - 2
            scores[player] = scores.get(player, 0) + 1
        last[player] = self_counter
        accepted += 1
        order.append(f"{player}:{self_counter}:{source_line}")
    return {
        "status": "ok",
        "accepted": accepted,
        "rejected": rejected,
        "scores": [{"player": p, "score": scores[p]} for p in sorted(scores)],
        "zones": [{"zone": z, "owner": zones[z]} for z in sorted(zones)],
        "order": order,
    }


def run_arbiter(args):
    subprocess.run(["/app/scripts/build.sh"], cwd="/app", check=True)
    return subprocess.run(["/app/bin/arbiter", *map(str, args)], cwd="/app", text=True, capture_output=True)


def assert_report(path, expected):
    actual = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected


def test_arena_report_uses_the_official_replay_contract(tmp_path):
    """A mixed arena stream is adjudicated by the public contract."""
    events = [
        ("bob", 5, {"alice": 1, "bob": 1}, ("move", "hill", 3)),
        ("alice", 10, {"alice": 1, "bob": 0}, ("move", "hill", 4)),
        ("carol", 8, {"alice": 1, "bob": 0, "carol": 1}, ("move", "lake", 2)),
        ("alice", 8, {"alice": 2, "bob": 0, "carol": 0}, ("move", "lake", 1)),
        ("bob", 12, {"alice": 1, "bob": 2, "carol": 0}, ("timeout", "alice")),
    ]
    event_path = tmp_path / "match.terms"
    report_path = tmp_path / "report.json"
    write_events(event_path, events)
    result = run_arbiter(["adjudicate", event_path, report_path])
    assert result.returncode == 0, result.stderr
    assert_report(report_path, reference(events))


def test_arena_report_preserves_counter_and_timeout_rules(tmp_path):
    """The official report matches the contract when counters skip and views are stale."""
    events = [
        ("alice", 1, {"alice": 1, "bob": 0}, ("move", "gate", 5)),
        ("bob", 2, {"alice": 1, "bob": 1}, ("move", "yard", 2)),
        ("alice", 3, {"alice": 2, "bob": 1}, ("move", "gate", 1)),
        ("bob", 4, {"alice": 1, "bob": 2}, ("timeout", "alice")),
        ("carol", 5, {"alice": 2, "bob": 1, "carol": 3}, ("move", "yard", 7)),
    ]
    event_path = tmp_path / "round.terms"
    report_path = tmp_path / "report.json"
    write_events(event_path, events)
    result = run_arbiter(["adjudicate", event_path, report_path])
    assert result.returncode == 0, result.stderr
    assert_report(report_path, reference(events))


def test_concurrent_cards_use_millis_player_and_source_tiebreakers(tmp_path):
    """Unrelated arena cards are ordered only by the public simultaneous-card keys."""
    events = [
        ("carol", 40, {"alice": 0, "bob": 0, "carol": 1}, ("move", "spire", 2)),
        ("bob", 10, {"alice": 0, "bob": 1, "carol": 0}, ("move", "gate", 4)),
        ("alice", 10, {"alice": 1, "bob": 0, "carol": 0}, ("move", "gate", 5)),
        ("dave", 10, {"alice": 0, "bob": 0, "carol": 0, "dave": 1}, ("move", "yard", 3)),
        ("alice", 50, {"alice": 2, "bob": 1, "carol": 1, "dave": 1}, ("timeout", "bob")),
    ]
    event_path = tmp_path / "concurrent.terms"
    report_path = tmp_path / "report.json"
    write_events(event_path, events)
    result = run_arbiter(["adjudicate", event_path, report_path])
    assert result.returncode == 0, result.stderr
    assert_report(report_path, reference(events))


def test_self_counter_skip_is_rejected_without_advancing_clock(tmp_path):
    """A skipped self counter is rejected and does not consume the player's next counter."""
    events = [
        ("alice", 1, {"alice": 1, "bob": 0}, ("move", "gate", 5)),
        ("alice", 2, {"alice": 3, "bob": 0}, ("move", "yard", 7)),
        ("bob", 3, {"alice": 1, "bob": 1}, ("timeout", "alice")),
        ("alice", 4, {"alice": 2, "bob": 1}, ("move", "yard", 2)),
    ]
    event_path = tmp_path / "skipped.terms"
    report_path = tmp_path / "report.json"
    write_events(event_path, events)
    result = run_arbiter(["adjudicate", event_path, report_path])
    assert result.returncode == 0, result.stderr
    assert_report(report_path, reference(events))


def test_checkpoint_count_past_stream_is_rejected(tmp_path):
    """A checkpoint beyond the ordered stream writes a rejected report and fails."""
    events = [
        ("alice", 1, {"alice": 1, "bob": 0}, ("move", "gate", 5)),
        ("bob", 2, {"alice": 1, "bob": 1}, ("move", "yard", 2)),
    ]
    event_path = tmp_path / "short.terms"
    state_path = tmp_path / "state.term"
    report_path = tmp_path / "report.json"
    write_events(event_path, events)
    result = run_arbiter(["checkpoint", event_path, 3, state_path, report_path])
    assert result.returncode != 0
    actual = json.loads(report_path.read_text(encoding="utf-8"))
    assert actual["status"] == "rejected"
    assert actual["accepted"] == 0
    assert actual["rejected"] == 0


def test_timeout_requires_exact_victim_counter_view(tmp_path):
    """A stale timeout is rejected even when the reporter's own counter is next."""
    events = [
        ("alice", 1, {"alice": 1, "bob": 0}, ("move", "gate", 5)),
        ("bob", 2, {"alice": 0, "bob": 1}, ("timeout", "alice")),
        ("bob", 3, {"alice": 1, "bob": 1}, ("move", "yard", 2)),
        ("alice", 4, {"alice": 2, "bob": 1}, ("move", "yard", 1)),
    ]
    event_path = tmp_path / "stale-timeout.terms"
    report_path = tmp_path / "report.json"
    write_events(event_path, events)
    result = run_arbiter(["adjudicate", event_path, report_path])
    assert result.returncode == 0, result.stderr
    assert_report(report_path, reference(events))


def test_checkpoint_resume_matches_one_shot_adjudication(tmp_path):
    """A resumed official report is identical to the one-shot report."""
    events = [
        ("alice", 3, {"alice": 1, "bob": 0, "carol": 0}, ("move", "a", 2)),
        ("bob", 1, {"alice": 1, "bob": 1, "carol": 0}, ("move", "b", 3)),
        ("carol", 2, {"alice": 1, "bob": 0, "carol": 1}, ("move", "a", 4)),
        ("alice", 4, {"alice": 2, "bob": 1, "carol": 1}, ("timeout", "bob")),
        ("bob", 6, {"alice": 2, "bob": 2, "carol": 1}, ("move", "c", 5)),
    ]
    event_path = tmp_path / "events.terms"
    state_path = tmp_path / "state.term"
    checkpoint_report = tmp_path / "checkpoint.json"
    resumed_report = tmp_path / "resumed.json"
    write_events(event_path, events)
    checkpoint = run_arbiter(["checkpoint", event_path, 3, state_path, checkpoint_report])
    assert checkpoint.returncode == 0, checkpoint.stderr
    assert_report(checkpoint_report, reference_ordered_prefix(events, 3))
    resumed = run_arbiter(["resume", event_path, state_path, resumed_report])
    assert resumed.returncode == 0, resumed.stderr
    assert_report(resumed_report, reference(events))


def test_resume_requires_the_saved_match_prefix(tmp_path):
    """A resume attempt refuses to continue from a different saved prefix."""
    events = [
        ("alice", 1, {"alice": 1, "bob": 0}, ("move", "gate", 2)),
        ("bob", 2, {"alice": 1, "bob": 1}, ("move", "gate", 3)),
        ("alice", 3, {"alice": 2, "bob": 1}, ("move", "tower", 4)),
    ]
    changed = [
        ("alice", 1, {"alice": 1, "bob": 0}, ("move", "gate", 7)),
        events[1],
        events[2],
    ]
    event_path = tmp_path / "events.terms"
    changed_path = tmp_path / "changed.terms"
    state_path = tmp_path / "state.term"
    report_path = tmp_path / "report.json"
    write_events(event_path, events)
    write_events(changed_path, changed)
    checkpoint = run_arbiter(["checkpoint", event_path, 2, state_path, tmp_path / "checkpoint.json"])
    assert checkpoint.returncode == 0, checkpoint.stderr
    resumed = run_arbiter(["resume", changed_path, state_path, report_path])
    assert resumed.returncode != 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "rejected"
