import hashlib
import json
import subprocess
from pathlib import Path

APP = Path("/app")
BIN = APP / "bin" / "rekor-gate"

REQUIRED_CLAIMS = [
    {"name": "source", "value": "repo:payments"},
    {"name": "builder", "value": "trusted-builder"},
    {"name": "workflow", "value": "release"},
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def h(data):
    return hashlib.sha256(data).hexdigest()


def witness_sig(wid, log_id, checkpoint, salt=""):
    msg = f"{wid}:{log_id}:{checkpoint['size']}:{checkpoint['root']}:{salt}".encode()
    return h(msg)


def witness(wid, log_id, checkpoint, signed_at, salt=""):
    return {"id": wid, "signed_at": signed_at, "signature": witness_sig(wid, log_id, checkpoint, salt)}


def leaf(payload_hex):
    return h(b"\x00" + bytes.fromhex(payload_hex))


def node(left, right):
    return h(b"\x01" + bytes.fromhex(left) + bytes.fromhex(right))


def level_up(level):
    out = []
    i = 0
    while i < len(level):
        if i + 1 < len(level):
            out.append(node(level[i], level[i + 1]))
        else:
            out.append(level[i])
        i += 2
    return out


def proof(payloads, index):
    level = [leaf(x) for x in payloads]
    path = []
    idx = index
    while len(level) > 1:
        if idx % 2 == 0:
            if idx + 1 < len(level):
                path.append(level[idx + 1])
        else:
            path.append(level[idx - 1])
        idx //= 2
        level = level_up(level)
    return level[0], path


def identity(**overrides):
    base = {
        "issuer": "fulcio.example",
        "subject": "repo:payments",
        "san": "payments@example.test",
        "key": "key-a",
        "not_before": 900,
        "not_after": 1200,
    }
    base.update(overrides)
    return base


def entry(n, ident=None, claims=None):
    return {
        "signature_time": 980 + n,
        "identity": ident or identity(),
        "artifact": {
            "z_meta": {"release": n, "channels": ["stable", "canary"]},
            "claims": claims if claims is not None else list(REQUIRED_CLAIMS),
            "digest": h(f"artifact:{n}".encode()),
            "a_nested": {"inner": {"b": n, "a": f"release-{n}"}},
        },
    }


def payload_for(ent):
    return h(canonical(ent))


def bundle(uuid, log_id, ent, payloads, index, integrated, prior=None, witnesses=None):
    ph = payload_for(ent)
    payloads = list(payloads)
    payloads[index] = ph
    root, path = proof(payloads, index)
    return {
        "uuid": uuid,
        "log_id": log_id,
        "integrated_time": integrated,
        "checkpoint": {"size": len(payloads), "root": root},
        "entry": ent,
        "payload_hash": ph,
        "inclusion": {"index": index, "hashes": path},
        "consistency": ([] if prior is None else [{"size": prior[0], "root": prior[1]}]),
        "witnesses": [] if witnesses is None else list(witnesses),
    }


def policy(log_ids, min_tree_size=2, allow=None, min_claims=2, trusted_witnesses=None, witness_quorum=0, revoked_payloads=None):
    return {
        "logs": [{"id": log_id, "min_tree_size": min_tree_size} for log_id in log_ids],
        "allow": allow or [{"issuer": "fulcio.example", "san": "payments@example.test"}],
        "required_claims": list(REQUIRED_CLAIMS),
        "min_claims": min_claims,
        "trusted_witnesses": [] if trusted_witnesses is None else list(trusted_witnesses),
        "witness_quorum": witness_quorum,
        "revoked_payloads": [] if revoked_payloads is None else list(revoked_payloads),
    }


def write_json(path, value):
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n")


def run_gate(tmp_path, b, p, state=None, now=1500):
    bundle_path = tmp_path / f"bundle-{b['uuid']}.json"
    policy_path = tmp_path / "policy.json"
    state_path = state or (tmp_path / "state.json")
    report_path = tmp_path / f"report-{b['uuid']}.json"
    write_json(bundle_path, b)
    write_json(policy_path, p)
    before = bundle_path.read_bytes(), policy_path.read_bytes()
    proc = subprocess.run(
        [
            str(BIN),
            "--bundle",
            str(bundle_path),
            "--policy",
            str(policy_path),
            "--time",
            str(now),
            "--state",
            str(state_path),
            "--out",
            str(report_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bundle_path.read_bytes() == before[0]
    assert policy_path.read_bytes() == before[1]
    report_text = report_path.read_text()
    assert report_text.endswith("\n")
    return proc, json.loads(report_text), state_path


def setup_module(module):
    subprocess.run(["make", "-C", str(APP)], check=True)


def test_gate_admits_two_allowed_logs_and_records_independent_state(tmp_path):
    """Multiple trusted logs are admitted without one checkpoint replacing another."""
    log_a = h(b"log-A")
    log_b = h(b"log-B")
    first = bundle("00112233445566778899aabbccddeeff", log_a, entry(7), [h(b"a0"), h(b"a1"), h(b"a2")], 2, 1005)
    proc, report, state_path = run_gate(tmp_path, first, policy([log_a, log_b]))
    assert proc.returncode == 0, proc.stderr
    assert report == {"admitted": True, "reason": "admitted", "uuid": first["uuid"], "checkpoint": first["checkpoint"]}

    second = bundle("11112222333344445555666677778888", log_b, entry(8), [h(b"b0"), h(b"b1")], 1, 1006)
    proc2, report2, _ = run_gate(tmp_path, second, policy([log_a, log_b]), state=state_path)
    assert proc2.returncode == 0, proc2.stderr
    assert report2["admitted"] is True

    state = json.loads(state_path.read_text())
    assert set(state) == {"logs"}
    assert set(state["logs"]) == {log_a, log_b}
    assert state["logs"][log_a]["checkpoint"] == first["checkpoint"]
    assert state["logs"][log_b]["checkpoint"] == second["checkpoint"]
    assert state["logs"][log_a]["entries"] == {first["uuid"]: {"payload_hash": first["payload_hash"]}}
    assert state["logs"][log_b]["entries"] == {second["uuid"]: {"payload_hash": second["payload_hash"]}}


def test_gate_requires_thresholded_distinct_artifact_claims(tmp_path):
    """Admission counts distinct required artifact claim pairs from entry.artifact.claims."""
    log_id = h(b"log-C")
    one_claim_twice = [REQUIRED_CLAIMS[0], dict(REQUIRED_CLAIMS[0]), {"name": "note", "value": "ignored"}]
    weak = bundle("22222222222222222222222222222222", log_id, entry(20, claims=one_claim_twice), [h(b"c0"), h(b"c1")], 0, 1010)
    proc, report, state_path = run_gate(tmp_path, weak, policy([log_id], min_claims=2))
    assert proc.returncode == 0, proc.stderr
    assert report["admitted"] is False
    assert report["reason"] == "rejected"
    assert not state_path.exists()

    enough = bundle("33333333333333333333333333333333", log_id, entry(21, claims=REQUIRED_CLAIMS[:2]), [h(b"c2"), h(b"c3")], 1, 1011)
    proc2, report2, state_path2 = run_gate(tmp_path, enough, policy([log_id], min_claims=2))
    assert proc2.returncode == 0, proc2.stderr
    assert report2["admitted"] is True
    assert json.loads(state_path2.read_text())["logs"][log_id]["entries"][enough["uuid"]]["payload_hash"] == enough["payload_hash"]


def test_gate_rejects_untrusted_log_without_state_mutation(tmp_path):
    """Evidence from a log absent from policy.logs is rejected and never creates trusted state."""
    trusted = h(b"trusted-log")
    untrusted = h(b"untrusted-log")
    b = bundle("34343434343434343434343434343434", untrusted, entry(24), [h(b"u0"), h(b"u1")], 1, 1014)
    proc, report, state_path = run_gate(tmp_path, b, policy([trusted]))
    assert proc.returncode == 0, proc.stderr
    assert report == {"admitted": False, "reason": "rejected", "uuid": b["uuid"], "checkpoint": None}
    assert not state_path.exists()


def test_gate_uses_canonical_nested_entry_json_for_payload_hash(tmp_path):
    """Canonicalization sorts keys recursively while preserving array order inside entry."""
    log_id = h(b"log-D")
    ent = entry(30)
    ent["artifact"]["claims"] = [REQUIRED_CLAIMS[2], REQUIRED_CLAIMS[0], REQUIRED_CLAIMS[1]]
    ent["artifact"]["z_meta"] = {"z": {"later": [3, 2, 1]}, "a": {"earlier": True}}
    b = bundle("44444444444444444444444444444444", log_id, ent, [h(b"d0"), h(b"d1"), h(b"d2"), h(b"d3")], 3, 1020)
    proc, report, _ = run_gate(tmp_path, b, policy([log_id]))
    assert proc.returncode == 0, proc.stderr
    assert report["admitted"] is True


def test_gate_extends_only_the_matching_log_checkpoint(tmp_path):
    """Consistency checks are scoped to the log being admitted, not to global state."""
    log_a = h(b"log-E-a")
    log_b = h(b"log-E-b")
    first_a = bundle("55555555555555555555555555555555", log_a, entry(40), [h(b"e0"), h(b"e1"), h(b"e2")], 0, 1030)
    proc, report, state_path = run_gate(tmp_path, first_a, policy([log_a, log_b]))
    assert proc.returncode == 0 and report["admitted"] is True

    first_b = bundle("66666666666666666666666666666666", log_b, entry(41), [h(b"f0"), h(b"f1")], 1, 1031)
    proc2, report2, _ = run_gate(tmp_path, first_b, policy([log_a, log_b]), state=state_path)
    assert proc2.returncode == 0 and report2["admitted"] is True

    extension_a = bundle(
        "77777777777777777777777777777777",
        log_a,
        entry(42),
        [h(f"grow-a:{i}".encode()) for i in range(6)],
        5,
        1032,
        prior=(first_a["checkpoint"]["size"], first_a["checkpoint"]["root"]),
    )
    proc3, report3, _ = run_gate(tmp_path, extension_a, policy([log_a, log_b]), state=state_path)
    assert proc3.returncode == 0, proc3.stderr
    assert report3["admitted"] is True
    state = json.loads(state_path.read_text())
    assert state["logs"][log_a]["checkpoint"] == extension_a["checkpoint"]
    assert state["logs"][log_b]["checkpoint"] == first_b["checkpoint"]


def test_gate_rejects_invalid_consistency_proof_without_state_mutation(tmp_path):
    """A larger checkpoint must carry the saved checkpoint size and root in consistency."""
    log_id = h(b"log-E-consistency")
    first = bundle("70707070707070707070707070707070", log_id, entry(44), [h(b"q0"), h(b"q1"), h(b"q2")], 2, 1034)
    proc, report, state_path = run_gate(tmp_path, first, policy([log_id]))
    assert proc.returncode == 0 and report["admitted"] is True
    saved_state = state_path.read_text()

    bad = bundle(
        "71717171717171717171717171717171",
        log_id,
        entry(45),
        [h(f"extend:{i}".encode()) for i in range(6)],
        4,
        1035,
        prior=(first["checkpoint"]["size"], h(b"wrong-prior-root")),
    )
    proc2, report2, _ = run_gate(tmp_path, bad, policy([log_id]), state=state_path)
    assert proc2.returncode == 0, proc2.stderr
    assert report2["admitted"] is False
    assert report2["reason"] == "rejected"
    assert state_path.read_text() == saved_state


def test_gate_rejects_cross_log_rollback_without_state_mutation(tmp_path):
    """A rollback in one log is rejected while all other saved log state remains byte-identical."""
    log_a = h(b"log-F-a")
    log_b = h(b"log-F-b")
    a = bundle("88888888888888888888888888888888", log_a, entry(50), [h(b"g0"), h(b"g1"), h(b"g2"), h(b"g3")], 2, 1040)
    b = bundle("99999999999999999999999999999999", log_b, entry(51), [h(b"h0"), h(b"h1"), h(b"h2")], 0, 1041)
    proc, report, state_path = run_gate(tmp_path, a, policy([log_a, log_b]))
    assert proc.returncode == 0 and report["admitted"] is True
    proc2, report2, _ = run_gate(tmp_path, b, policy([log_a, log_b]), state=state_path)
    assert proc2.returncode == 0 and report2["admitted"] is True
    saved_state = state_path.read_text()

    rollback = bundle("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", log_a, entry(52), [h(b"small0"), h(b"small1")], 0, 1042)
    proc3, report3, _ = run_gate(tmp_path, rollback, policy([log_a, log_b]), state=state_path)
    assert proc3.returncode == 0, proc3.stderr
    assert report3["admitted"] is False
    assert report3["reason"] == "rejected"
    assert state_path.read_text() == saved_state


def test_gate_scopes_duplicate_uuid_conflicts_to_each_log(tmp_path):
    """The same UUID may appear on another trusted log, but not with changed payload on one log."""
    log_a = h(b"log-G-a")
    log_b = h(b"log-G-b")
    uuid = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    first = bundle(uuid, log_a, entry(60), [h(b"i0"), h(b"i1"), h(b"i2")], 1, 1050)
    proc, report, state_path = run_gate(tmp_path, first, policy([log_a, log_b]))
    assert proc.returncode == 0 and report["admitted"] is True

    same_uuid_other_log = bundle(uuid, log_b, entry(61), [h(b"j0"), h(b"j1")], 0, 1051)
    proc2, report2, _ = run_gate(tmp_path, same_uuid_other_log, policy([log_a, log_b]), state=state_path)
    assert proc2.returncode == 0, proc2.stderr
    assert report2["admitted"] is True
    saved_state = state_path.read_text()

    changed_same_log = bundle(
        uuid,
        log_a,
        entry(62),
        [h(f"k:{i}".encode()) for i in range(5)],
        4,
        1052,
        prior=(first["checkpoint"]["size"], first["checkpoint"]["root"]),
    )
    proc3, report3, _ = run_gate(tmp_path, changed_same_log, policy([log_a, log_b]), state=state_path)
    assert proc3.returncode == 0
    assert report3["admitted"] is False
    assert state_path.read_text() == saved_state


def test_gate_checks_allow_rule_conjunction_and_evidence_times(tmp_path):
    """Allow rules are conjunctive, and signature/integration times must fit certificate validity."""
    log_id = h(b"log-H")
    allow = [{"issuer": "fulcio.example", "subject": "repo:payments", "san": "payments@example.test"}]
    bad_identity = identity(san="other@example.test", not_after=2000)
    bad_identity_bundle = bundle("cccccccccccccccccccccccccccccccc", log_id, entry(70, ident=bad_identity), [h(b"l0"), h(b"l1")], 0, 1060)
    proc, report, state_path = run_gate(tmp_path, bad_identity_bundle, policy([log_id], allow=allow))
    assert proc.returncode == 0
    assert report["admitted"] is False
    assert not state_path.exists()

    too_early_signature = entry(71, ident=identity(not_before=900, not_after=1000))
    too_early_signature["signature_time"] = 899
    early = bundle("dddddddddddddddddddddddddddddddd", log_id, too_early_signature, [h(b"m0"), h(b"m1")], 1, 950)
    proc2, report2, state_path2 = run_gate(tmp_path, early, policy([log_id]), now=950)
    assert proc2.returncode == 0
    assert report2["admitted"] is False
    assert not state_path2.exists()

    expired_at_integration = entry(71, ident=identity(not_before=900, not_after=1000))
    expired_at_integration["signature_time"] = 990
    late = bundle("d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1", log_id, expired_at_integration, [h(b"m2"), h(b"m3")], 1, 1001)
    proc3, report3, state_path3 = run_gate(tmp_path, late, policy([log_id]), now=1500)
    assert proc3.returncode == 0
    assert report3["admitted"] is False
    assert not state_path3.exists()

    future_integrated = entry(72, ident=identity(not_before=900, not_after=2000))
    future_integrated["signature_time"] = 1100
    future = bundle("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", log_id, future_integrated, [h(b"n0"), h(b"n1")], 0, 1105)
    proc4, report4, state_path4 = run_gate(tmp_path, future, policy([log_id]), now=1104)
    assert proc4.returncode == 0
    assert report4["admitted"] is False
    assert not state_path4.exists()

    signature_after_integration = entry(73, ident=identity(not_before=900, not_after=2000))
    signature_after_integration["signature_time"] = 1101
    inverted = bundle("e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1", log_id, signature_after_integration, [h(b"n2"), h(b"n3")], 0, 1100)
    proc5, report5, state_path5 = run_gate(tmp_path, inverted, policy([log_id]), now=1500)
    assert proc5.returncode == 0
    assert report5["admitted"] is False
    assert not state_path5.exists()


def test_gate_treats_uppercase_hex_as_malformed(tmp_path):
    """Hex identifiers and hashes must be lowercase, not normalized."""
    log_id = h(b"log-I")
    b = bundle("ffffffffffffffffffffffffffffffff", log_id, entry(80), [h(b"o0"), h(b"o1")], 0, 1070)
    b["checkpoint"]["root"] = b["checkpoint"]["root"].upper()
    proc, report, state_path = run_gate(tmp_path, b, policy([log_id]))
    assert proc.returncode == 2
    assert report == {"admitted": False, "reason": "malformed", "uuid": None, "checkpoint": None}
    assert not state_path.exists()


def test_gate_handles_repeated_odd_tree_promotions(tmp_path):
    """Inclusion proofs remain valid when unpaired nodes are promoted at several tree levels."""
    log_id = h(b"log-J")
    b = bundle("0123456789abcdef0123456789abcdef", log_id, entry(90), [h(f"odd:{i}".encode()) for i in range(9)], 8, 1080)
    proc, report, state_path = run_gate(tmp_path, b, policy([log_id]))
    assert proc.returncode == 0, proc.stderr
    assert report["admitted"] is True
    assert json.loads(state_path.read_text())["logs"][log_id]["checkpoint"] == b["checkpoint"]


def test_gate_requires_distinct_trusted_witness_quorum_for_checkpoint(tmp_path):
    """Checkpoint witness attestations must be signed by distinct trusted witness IDs."""
    log_id = h(b"log-K")
    base = bundle("10101010101010101010101010101010", log_id, entry(100), [h(b"w0"), h(b"w1"), h(b"w2")], 1, 1090)
    weak = dict(base)
    weak["witnesses"] = [
        witness("w1", log_id, base["checkpoint"], 1091),
        witness("w1", log_id, base["checkpoint"], 1092),
        witness("outsider", log_id, base["checkpoint"], 1091),
    ]
    proc, report, state_path = run_gate(tmp_path, weak, policy([log_id], trusted_witnesses=["w1", "w2"], witness_quorum=2))
    assert proc.returncode == 0, proc.stderr
    assert report["admitted"] is False
    assert not state_path.exists()

    strong = dict(base)
    strong["witnesses"] = [
        witness("w1", log_id, base["checkpoint"], 1091),
        witness("w2", log_id, base["checkpoint"], 1092),
    ]
    proc2, report2, state_path2 = run_gate(tmp_path, strong, policy([log_id], trusted_witnesses=["w1", "w2"], witness_quorum=2))
    assert proc2.returncode == 0, proc2.stderr
    assert report2["admitted"] is True
    assert json.loads(state_path2.read_text())["logs"][log_id]["witnesses"] == {"w1": strong["checkpoint"], "w2": strong["checkpoint"]}


def test_gate_rejects_stale_or_misbound_checkpoint_witnesses(tmp_path):
    """Witness signatures bind log ID, root, size, and freshness window."""
    log_a = h(b"log-L-a")
    log_b = h(b"log-L-b")
    b = bundle("20202020202020202020202020202020", log_a, entry(110), [h(b"x0"), h(b"x1")], 0, 1100)
    misbound = dict(b)
    misbound["witnesses"] = [witness("w1", log_b, b["checkpoint"], 1101)]
    proc, report, state_path = run_gate(tmp_path, misbound, policy([log_a], trusted_witnesses=["w1"], witness_quorum=1))
    assert proc.returncode == 0
    assert report["admitted"] is False
    assert not state_path.exists()

    stale = dict(b)
    stale["witnesses"] = [witness("w1", log_a, b["checkpoint"], b["integrated_time"] - 1)]
    proc2, report2, state_path2 = run_gate(tmp_path, stale, policy([log_a], trusted_witnesses=["w1"], witness_quorum=1))
    assert proc2.returncode == 0
    assert report2["admitted"] is False
    assert not state_path2.exists()


def test_gate_rejects_revoked_payloads_before_state_mutation(tmp_path):
    """Policy revocations are checked against the canonical entry payload hash."""
    log_id = h(b"log-M")
    b = bundle("30303030303030303030303030303030", log_id, entry(120), [h(b"y0"), h(b"y1"), h(b"y2")], 2, 1110)
    proc, report, state_path = run_gate(tmp_path, b, policy([log_id], revoked_payloads=[b["payload_hash"]]))
    assert proc.returncode == 0, proc.stderr
    assert report["admitted"] is False
    assert report["checkpoint"] == b["checkpoint"]
    assert not state_path.exists()


def test_gate_preserves_witnesses_across_log_scoped_checkpoint_extension(tmp_path):
    """Extending one log updates only that log's checkpoint and witness snapshot."""
    log_a = h(b"log-N-a")
    log_b = h(b"log-N-b")
    a = bundle("40404040404040404040404040404040", log_a, entry(130), [h(b"n0"), h(b"n1")], 0, 1120)
    a["witnesses"] = [witness("wa", log_a, a["checkpoint"], 1121)]
    b = bundle("50505050505050505050505050505050", log_b, entry(131), [h(b"p0"), h(b"p1")], 1, 1122)
    b["witnesses"] = [witness("wb", log_b, b["checkpoint"], 1123)]
    pol = policy([log_a, log_b], trusted_witnesses=["wa", "wb"], witness_quorum=1)
    proc, report, state_path = run_gate(tmp_path, a, pol)
    assert proc.returncode == 0 and report["admitted"] is True
    proc2, report2, _ = run_gate(tmp_path, b, pol, state=state_path)
    assert proc2.returncode == 0 and report2["admitted"] is True

    ext = bundle("60606060606060606060606060606060", log_a, entry(132), [h(f"n-ext:{i}".encode()) for i in range(5)], 3, 1124, prior=(a["checkpoint"]["size"], a["checkpoint"]["root"]))
    ext["witnesses"] = [witness("wa", log_a, ext["checkpoint"], 1125)]
    proc3, report3, _ = run_gate(tmp_path, ext, pol, state=state_path)
    assert proc3.returncode == 0, proc3.stderr
    assert report3["admitted"] is True
    state = json.loads(state_path.read_text())
    assert state["logs"][log_a]["checkpoint"] == ext["checkpoint"]
    assert state["logs"][log_a]["witnesses"] == {"wa": ext["checkpoint"]}
    assert state["logs"][log_b]["checkpoint"] == b["checkpoint"]
    assert state["logs"][log_b]["witnesses"] == {"wb": b["checkpoint"]}
