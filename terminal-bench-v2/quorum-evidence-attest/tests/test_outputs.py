import json
import subprocess
from pathlib import Path

CMD = Path("/usr/local/sbin/qguard-attest")
PRIMARY_EVIDENCE = Path("/var/lib/qguard/evidence")
PRIMARY_REPORT = Path("/var/lib/qguard/audit/quorum-attestation.json")


def write_terms(base, config, events):
    base.mkdir(parents=True, exist_ok=True)
    nodes = ",".join(f"{{{name},{weight}}}" for name, weight in config["nodes"])
    zones = config.get("zones")
    zone_text = ""
    if zones:
        zone_pairs = ",".join(f"{node} => {zone}" for node, zone in zones.items())
        zone_text = f", zones => #{{{zone_pairs}}}"
    (base / "config.term").write_text(
        f"#{{lease_ms => {config['lease_ms']}, delay_ms => {config['delay_ms']}, nodes => [{nodes}]{zone_text}}}.\n",
        encoding="utf-8",
    )
    chunks = []
    for event in events:
        pairs = [
            f"{key} => {event[key]}"
            for key in ("at", "seq", "kind", "node", "candidate", "term", "until", "leader", "lease_until")
            if key in event
        ]
        chunks.append(f"#{{{', '.join(pairs)}}}")
    separator = ",\n "
    (base / "events.term").write_text(f"[{separator.join(chunks)}].\n", encoding="utf-8")


def run_attestation(tmp_path, name, config, events):
    scenario = tmp_path / name
    report = tmp_path / (name + ".json")
    write_terms(scenario, config, events)
    before = PRIMARY_REPORT.read_text(encoding="utf-8") if PRIMARY_REPORT.exists() else None
    completed = subprocess.run(
        [str(CMD), str(scenario), str(report)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    if before is not None:
        assert PRIMARY_REPORT.read_text(encoding="utf-8") == before
    return json.loads(report.read_text(encoding="utf-8"))


def ref_attestation(config, events):
    nodes = config["nodes"]
    weights = dict(nodes)
    zones = config.get("zones", {})
    up = {name: True for name, _ in nodes}
    fenced_until = {name: 0 for name, _ in nodes}
    term = 0
    leader = None
    lease_until = 0
    pending = None
    schedule = []
    promoted_by_term = {}

    def is_unfenced(node, at):
        return up.get(node, False) and at >= fenced_until.get(node, 0)

    def quorate(at):
        total = sum(weight for _, weight in nodes)
        active_nodes = [name for name, weight in nodes if is_unfenced(name, at) for _ in range(weight)]
        active_weight = len(active_nodes)
        if active_weight * 2 <= total:
            return False
        if len(nodes) >= 3:
            active_zones = {zones.get(name, name) for name, _ in nodes if is_unfenced(name, at)}
            return len(active_zones) >= 2
        return True

    def signer_quorate(signers):
        total = sum(weight for _, weight in nodes)
        active_weight = sum(weights[name] for name in set(signers))
        if active_weight * 2 <= total:
            return False
        if len(nodes) >= 3:
            return len({zones.get(name, name) for name in set(signers)}) >= 2
        return True

    def candidate(at):
        available = [name for name, _ in nodes if is_unfenced(name, at)]
        if not available:
            return None
        return min(available, key=lambda name: (-weights[name], name))

    def accept(node, new_term, at, new_lease=None):
        nonlocal leader, term, lease_until, pending
        leader = node
        term = new_term
        lease_until = at + config["lease_ms"] if new_lease is None else max(lease_until, new_lease)
        pending = None

    for event in events:
        if event["kind"] == "promote" and "term" in event:
            promoted_by_term.setdefault(event["term"], set()).add(event["node"])

    def due_before(at, inclusive):
        nonlocal pending
        while pending is not None and (pending < at or (inclusive and pending == at)):
            due = pending
            pending = None
            if quorate(due) and due >= lease_until:
                chosen = candidate(due)
                if chosen is not None:
                    new_term = term + 1
                    schedule.append({"action": "promote", "at": due, "node": chosen, "term": new_term})
                    accept(chosen, new_term, due)

    tagged = sorted(enumerate(events), key=lambda item: (item[1]["at"], item[1].get("seq", 0), item[0]))
    i = 0
    while i < len(tagged):
        at = tagged[i][1]["at"]
        due_before(at, False)
        group = []
        while i < len(tagged) and tagged[i][1]["at"] == at:
            group.append(tagged[i][1])
            i += 1
        entry_up = dict(up)
        entry_fenced = dict(fenced_until)

        def entry_unfenced(name, up_snapshot=entry_up, fence_snapshot=entry_fenced, batch_at=at):
            return up_snapshot.get(name, False) and batch_at >= fence_snapshot.get(name, 0)

        def cert_ok(candidate_node, cert_term, batch=tuple(group)):
            matching_witnesses = [
                ev["node"]
                for ev in batch
                if ev["kind"] == "witness" and ev["candidate"] == candidate_node and ev["term"] == cert_term
            ]
            withdrawals = {
                ev["node"]
                for ev in batch
                if ev["kind"] == "withdraw" and ev["candidate"] == candidate_node and ev["term"] == cert_term
            }
            if not matching_witnesses and not withdrawals:
                return True
            signers = {name for name in matching_witnesses if name not in withdrawals and entry_unfenced(name)}
            return signer_quorate(signers)

        for event in group:
            kind = event["kind"]
            node = event.get("node")
            event_term = event.get("term", term)
            if kind == "up":
                up[node] = True
            elif kind == "down":
                up[node] = False
            elif kind == "fence":
                fenced_until[node] = max(fenced_until.get(node, 0), event["until"])
            elif kind == "lease":
                if node == leader and event_term == term and is_unfenced(node, at):
                    lease_until = max(lease_until, at + config["lease_ms"])
            elif kind == "heartbeat":
                if node == leader and event_term == term and is_unfenced(node, at):
                    lease_until = max(lease_until, at + config["lease_ms"])
                elif event_term > term and is_unfenced(node, at) and quorate(at) and at >= lease_until and cert_ok(node, event_term):
                    accept(node, event_term, at)
            elif kind == "promote" and event_term > term and is_unfenced(node, at) and quorate(at) and at >= lease_until and cert_ok(node, event_term):
                accept(node, event_term, at)
            elif kind == "snapshot":
                snap_leader = event["leader"]
                if event_term > term and is_unfenced(snap_leader, at) and quorate(at) and at >= lease_until and cert_ok(snap_leader, event_term):
                    accept(snap_leader, event_term, at, event["lease_until"])
        due_before(at, True)
        if (leader is None or not is_unfenced(leader, at)) and pending is None and quorate(at):
            pending = max(at, lease_until) + config["delay_ms"]
    if pending is not None:
        due_before(pending, True)
    verdict = "insecure" if any(len(nodes_for_term) > 1 for nodes_for_term in promoted_by_term.values()) else "secure"
    return {"leader": leader, "term": term, "safe": True, "verdict": verdict, "schedule": schedule}


def assert_sorted_schema(report):
    assert list(report) == ["leader", "safe", "schedule", "term", "verdict"]
    assert report["safe"] is True
    for entry in report["schedule"]:
        assert list(entry) == ["action", "at", "node", "term"]


def assert_report_matches_reference(tmp_path, name, config, events):
    got = run_attestation(tmp_path, name, config, events)
    assert got == ref_attestation(config, events)
    assert_sorted_schema(got)
    second = run_attestation(tmp_path, name + "_again", config, list(reversed(events)))
    assert second == got


def test_primary_attestation_file():
    """The canonical audit attestation is present and follows the public schema."""
    assert PRIMARY_REPORT.exists()
    report = json.loads(PRIMARY_REPORT.read_text(encoding="utf-8"))
    config = {
        "lease_ms": 8,
        "delay_ms": 5,
        "nodes": [("atlas", 2), ("boreal", 1), ("cirrus", 1), ("draco", 2)],
        "zones": {"atlas": "west", "boreal": "west", "cirrus": "east", "draco": "east"},
    }
    events = [
        {"at": 0, "seq": 1, "kind": "promote", "node": "atlas", "term": 3},
        {"at": 3, "seq": 1, "kind": "fence", "node": "atlas", "until": 19},
        {"at": 4, "seq": 1, "kind": "lease", "node": "atlas", "term": 3},
        {"at": 5, "seq": 1, "kind": "down", "node": "boreal"},
        {"at": 7, "seq": 2, "kind": "snapshot", "leader": "draco", "term": 4, "lease_until": 16},
        {"at": 11, "seq": 1, "kind": "down", "node": "draco"},
        {"at": 18, "seq": 1, "kind": "up", "node": "boreal"},
        {"at": 18, "seq": 2, "kind": "promote", "node": "cirrus", "term": 5},
    ]
    assert report == ref_attestation(config, events)
    assert_sorted_schema(report)


def test_fence_blocks_candidate_and_quorum_until_expiry(tmp_path):
    """Fenced nodes are excluded from quorum and cannot be elected before their window expires."""
    config = {"lease_ms": 5, "delay_ms": 3, "nodes": [("alpha", 3), ("beta", 2), ("gamma", 2)], "zones": {"alpha": "a", "beta": "b", "gamma": "b"}}
    events = [
        {"at": 0, "seq": 1, "kind": "fence", "node": "alpha", "until": 9},
        {"at": 1, "seq": 1, "kind": "down", "node": "gamma"},
        {"at": 9, "seq": 1, "kind": "up", "node": "gamma"},
    ]
    report = run_attestation(tmp_path, "fence_quorum", config, events)
    assert report == ref_attestation(config, events)
    assert report["schedule"][0]["node"] == "alpha"
    assert report["schedule"][0]["at"] == 12


def test_snapshot_recovery_obeys_current_lease_and_zone_quorum(tmp_path):
    """Snapshot records advance leadership only after the active lease and zone quorum permit it."""
    config = {"lease_ms": 6, "delay_ms": 4, "nodes": [("alpha", 2), ("beta", 2), ("gamma", 1), ("delta", 1)], "zones": {"alpha": "west", "beta": "west", "gamma": "east", "delta": "east"}}
    events = [
        {"at": 0, "seq": 1, "kind": "promote", "node": "alpha", "term": 2},
        {"at": 4, "seq": 1, "kind": "snapshot", "leader": "beta", "term": 3, "lease_until": 20},
        {"at": 6, "seq": 1, "kind": "down", "node": "alpha"},
        {"at": 10, "seq": 1, "kind": "snapshot", "leader": "beta", "term": 3, "lease_until": 20},
        {"at": 19, "seq": 1, "kind": "down", "node": "gamma"},
        {"at": 21, "seq": 1, "kind": "up", "node": "gamma"},
    ]
    assert_report_matches_reference(tmp_path, "snapshot_recovery", config, events)


def test_same_term_promotes_are_insecure_even_when_reordered(tmp_path):
    """Verdict logic flags two nodes advertised for the same promoted term."""
    config = {"lease_ms": 6, "delay_ms": 2, "nodes": [("alpha", 1), ("beta", 1), ("gamma", 1)], "zones": {"alpha": "a", "beta": "b", "gamma": "b"}}
    events = [
        {"at": 0, "seq": 1, "kind": "promote", "node": "alpha", "term": 7},
        {"at": 0, "seq": 2, "kind": "promote", "node": "beta", "term": 7},
        {"at": 8, "seq": 1, "kind": "heartbeat", "node": "alpha", "term": 7},
    ]
    assert_report_matches_reference(tmp_path, "same_term_conflict", config, events)
    assert run_attestation(tmp_path, "same_term_conflict_check", config, events)["verdict"] == "insecure"


def test_weight_tiebreak_and_noncanonical_side_effects(tmp_path):
    """Scheduled promotions use weight then name ordering and leave the canonical audit untouched."""
    config = {"lease_ms": 4, "delay_ms": 3, "nodes": [("alpha", 1), ("beta", 3), ("gamma", 3)], "zones": {"alpha": "a", "beta": "b", "gamma": "c"}}
    events = [
        {"at": 0, "seq": 1, "kind": "down", "node": "alpha"},
        {"at": 1, "seq": 1, "kind": "up", "node": "alpha"},
    ]
    report = run_attestation(tmp_path, "weighted_candidate", config, events)
    assert report == ref_attestation(config, events)
    assert report["schedule"][0]["node"] == "beta"


def test_missing_seq_original_position_and_batch_scheduling(tmp_path):
    """Records without seq keep file order before scheduling from the post-batch state."""
    config = {"lease_ms": 7, "delay_ms": 4, "nodes": [("alpha", 2), ("beta", 2), ("gamma", 1)], "zones": {"alpha": "a", "beta": "b", "gamma": "b"}}
    events = [
        {"at": 4, "kind": "down", "node": "beta"},
        {"at": 4, "kind": "up", "node": "beta"},
    ]
    report = run_attestation(tmp_path, "missing_seq_due_batch", config, events)
    assert report == ref_attestation(config, events)
    assert report["schedule"] == [{"action": "promote", "at": 8, "node": "alpha", "term": 1}]
    assert report["leader"] == "alpha"
    assert report["term"] == 1


def test_higher_term_heartbeat_waits_for_lease_boundary(tmp_path):
    """A higher-term heartbeat cannot replace a leader until the active lease has expired."""
    config = {"lease_ms": 10, "delay_ms": 2, "nodes": [("alpha", 2), ("beta", 2), ("gamma", 1)], "zones": {"alpha": "a", "beta": "b", "gamma": "c"}}
    events = [
        {"at": 0, "seq": 1, "kind": "promote", "node": "alpha", "term": 4},
        {"at": 5, "seq": 1, "kind": "heartbeat", "node": "beta", "term": 5},
        {"at": 10, "seq": 1, "kind": "heartbeat", "node": "beta", "term": 5},
        {"at": 12, "seq": 1, "kind": "lease", "node": "beta", "term": 5},
        {"at": 17, "seq": 1, "kind": "heartbeat", "node": "gamma", "term": 6},
        {"at": 22, "seq": 1, "kind": "heartbeat", "node": "gamma", "term": 6},
    ]
    report = run_attestation(tmp_path, "lease_boundary_heartbeat", config, events)
    assert report == ref_attestation(config, events)
    assert report["leader"] == "gamma"
    assert report["term"] == 6


def test_two_node_majority_has_no_zone_requirement_and_fence_persists(tmp_path):
    """Two-node dossiers use only strict weighted majority, and up records do not clear fences."""
    config = {"lease_ms": 5, "delay_ms": 3, "nodes": [("alpha", 3), ("beta", 2)]}
    events = [
        {"at": 0, "seq": 1, "kind": "down", "node": "alpha"},
        {"at": 1, "seq": 1, "kind": "fence", "node": "alpha", "until": 8},
        {"at": 2, "seq": 1, "kind": "up", "node": "alpha"},
        {"at": 8, "seq": 1, "kind": "lease", "node": "beta", "term": 1},
    ]
    report = run_attestation(tmp_path, "two_node_fence", config, events)
    assert report == ref_attestation(config, events)
    assert report["schedule"] == [{"action": "promote", "at": 11, "node": "alpha", "term": 1}]


def test_failed_pending_is_cleared_before_rescheduling_on_new_evidence(tmp_path):
    """A due promotion that lacks quorum is discarded, then later evidence may schedule a fresh due time."""
    config = {"lease_ms": 6, "delay_ms": 4, "nodes": [("alpha", 2), ("beta", 2), ("gamma", 1), ("delta", 1)], "zones": {"alpha": "west", "beta": "west", "gamma": "east", "delta": "east"}}
    events = [
        {"at": 0, "seq": 1, "kind": "down", "node": "alpha"},
        {"at": 1, "seq": 1, "kind": "down", "node": "gamma"},
        {"at": 2, "seq": 1, "kind": "down", "node": "delta"},
        {"at": 4, "seq": 1, "kind": "up", "node": "gamma"},
        {"at": 9, "seq": 1, "kind": "up", "node": "delta"},
    ]
    report = run_attestation(tmp_path, "failed_pending_reschedule", config, events)
    assert report == ref_attestation(config, events)
    assert report["schedule"] == [{"action": "promote", "at": 13, "node": "beta", "term": 1}]


def test_withdrawn_and_fenced_witnesses_do_not_certify_higher_term_promote(tmp_path):
    """Same-batch certificate evidence gates observed higher-term promotions by signer weight and zone."""
    config = {"lease_ms": 6, "delay_ms": 4, "nodes": [("alpha", 2), ("beta", 2), ("gamma", 1), ("delta", 1)], "zones": {"alpha": "west", "beta": "west", "gamma": "east", "delta": "east"}}
    events = [
        {"at": 0, "seq": 1, "kind": "promote", "node": "alpha", "term": 2},
        {"at": 8, "seq": 1, "kind": "fence", "node": "gamma", "until": 20},
        {"at": 9, "seq": 1, "kind": "promote", "node": "beta", "term": 3},
        {"at": 9, "seq": 2, "kind": "witness", "node": "alpha", "candidate": "beta", "term": 3},
        {"at": 9, "seq": 3, "kind": "witness", "node": "gamma", "candidate": "beta", "term": 3},
        {"at": 9, "seq": 4, "kind": "witness", "node": "delta", "candidate": "beta", "term": 3},
        {"at": 9, "seq": 5, "kind": "withdraw", "node": "delta", "candidate": "beta", "term": 3},
        {"at": 14, "seq": 1, "kind": "promote", "node": "beta", "term": 3},
        {"at": 14, "seq": 2, "kind": "witness", "node": "alpha", "candidate": "beta", "term": 3},
        {"at": 14, "seq": 3, "kind": "witness", "node": "beta", "candidate": "beta", "term": 3},
        {"at": 14, "seq": 4, "kind": "witness", "node": "delta", "candidate": "beta", "term": 3},
    ]
    report = run_attestation(tmp_path, "withdrawn_witness_promote", config, events)
    assert report == ref_attestation(config, events)
    assert report["leader"] == "beta"
    assert report["term"] == 3


def test_snapshot_certificate_uses_entry_state_and_candidate_leader(tmp_path):
    """Snapshot acceptance uses a certificate for the snapshot leader evaluated before batch mutations."""
    config = {"lease_ms": 5, "delay_ms": 3, "nodes": [("alpha", 2), ("beta", 2), ("gamma", 2)], "zones": {"alpha": "a", "beta": "b", "gamma": "c"}}
    events = [
        {"at": 0, "seq": 1, "kind": "promote", "node": "alpha", "term": 1},
        {"at": 6, "seq": 1, "kind": "down", "node": "gamma"},
        {"at": 6, "seq": 2, "kind": "snapshot", "leader": "beta", "term": 2, "lease_until": 20},
        {"at": 6, "seq": 3, "kind": "witness", "node": "alpha", "candidate": "beta", "term": 2},
        {"at": 6, "seq": 4, "kind": "witness", "node": "gamma", "candidate": "beta", "term": 2},
        {"at": 21, "seq": 1, "kind": "snapshot", "leader": "beta", "term": 2, "lease_until": 30},
        {"at": 21, "seq": 2, "kind": "witness", "node": "alpha", "candidate": "beta", "term": 2},
        {"at": 21, "seq": 3, "kind": "witness", "node": "beta", "candidate": "beta", "term": 2},
    ]
    report = run_attestation(tmp_path, "snapshot_certificate", config, events)
    assert report == ref_attestation(config, events)
    assert report["leader"] == "beta"
    assert report["term"] == 2


def test_duplicate_witnesses_do_not_create_weight_and_legacy_observation_still_works(tmp_path):
    """Duplicate signers count once when certificate records appear; batches without certificate evidence keep legacy observation semantics."""
    config = {"lease_ms": 4, "delay_ms": 2, "nodes": [("alpha", 1), ("beta", 1), ("gamma", 1)], "zones": {"alpha": "a", "beta": "b", "gamma": "c"}}
    events = [
        {"at": 0, "seq": 1, "kind": "promote", "node": "alpha", "term": 1},
        {"at": 5, "seq": 1, "kind": "heartbeat", "node": "beta", "term": 2},
        {"at": 5, "seq": 2, "kind": "witness", "node": "beta", "candidate": "beta", "term": 2},
        {"at": 5, "seq": 3, "kind": "witness", "node": "beta", "candidate": "beta", "term": 2},
        {"at": 10, "seq": 1, "kind": "heartbeat", "node": "gamma", "term": 3},
    ]
    report = run_attestation(tmp_path, "duplicate_witness_legacy", config, events)
    assert report == ref_attestation(config, events)
    assert report["leader"] == "gamma"
    assert report["term"] == 3
