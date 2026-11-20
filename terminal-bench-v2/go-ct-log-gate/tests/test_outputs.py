import hashlib
import hmac
import json
import re
import subprocess
from pathlib import Path

APP = Path("/app/ctgate")
BIN = APP / "bin" / "ctgate"


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def leaf_hash(evidence):
    return hashlib.sha256(b"\x00" + canonical(evidence)).digest()


def node_hash(left, right):
    return hashlib.sha256(b"\x01" + left + right).digest()


def split_point(n):
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def tree_root(leaves):
    if not leaves:
        return hashlib.sha256(b"").digest()
    if len(leaves) == 1:
        return leaves[0]
    k = split_point(len(leaves))
    return node_hash(tree_root(leaves[:k]), tree_root(leaves[k:]))


def audit_path(leaves, index):
    if len(leaves) == 1:
        return []
    k = split_point(len(leaves))
    if index < k:
        return audit_path(leaves[:k], index) + [tree_root(leaves[k:]).hex()]
    return audit_path(leaves[k:], index - k) + [tree_root(leaves[:k]).hex()]


def sign_sth(key, log_id, size, root, timestamp):
    msg = f"ctgate-sth-v1\n{log_id}\n{size}\n{root}\n{timestamp}".encode()
    return hmac.HMAC(key, msg, hashlib.sha256).hexdigest()


def sign_checkpoint(key, log_id, size, root, timestamp):
    msg = f"ctgate-checkpoint-v1\n{log_id}\n{size}\n{root}\n{timestamp}".encode()
    return hmac.HMAC(key, msg, hashlib.sha256).hexdigest()


def sign_revocation(key, issuer, serial, status, timestamp):
    msg = f"ctgate-revocation-v1\n{issuer}\n{serial}\n{status}\n{timestamp}".encode()
    return hmac.HMAC(key, msg, hashlib.sha256).hexdigest()


NETWORK_SYSCALL = re.compile(
    r"^(?:\d+\s+)?(?:accept4?|bind|connect|getpeername|getsockname|getsockopt|"
    r"listen|recv(?:from|msg)?|recvmmsg|send(?:mmsg|msg|to)?|setsockopt|shutdown|"
    r"socket(?:pair)?)\("
)


def network_syscall_lines(trace):
    """Return only traced network syscalls, ignoring signal/runtime bookkeeping noise."""
    return [line for line in trace.splitlines() if NETWORK_SYSCALL.match(line.strip())]


def sth(key, log_id, leaves, timestamp):
    root = tree_root(leaves).hex()
    size = len(leaves)
    return {
        "log_id": log_id,
        "tree_size": size,
        "timestamp": timestamp,
        "root_hash": root,
        "signature": sign_sth(key, log_id, size, root, timestamp),
    }


def entry(i, log_id="alpha"):
    # Deliberately insert keys in noncanonical order; the public contract defines the hash order.
    evidence = {
        "serial": f"A-{i:03d}",
        "dns": [f"host{i}.example.test", "example.test"],
        "issuer": "Example Offline CA",
        "not_after": 1_720_000_000 + i,
        "subject": f"host{i}.example.test",
        "not_before": 1_700_000_000,
    }
    return {"id": f"leaf-{i}", "log_id": log_id, "leaf_index": i, "evidence": evidence}


def trust_file(path, key):
    data = {
        "logs": [
            {
                "log_id": "alpha",
                "key": key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
            }
        ]
    }
    path.write_text(json.dumps(data, indent=2) + "\n")


def build_binary():
    subprocess.run([str(APP / "scripts" / "build.sh")], check=True, cwd=APP)
    assert BIN.exists()


def run_gate(tmp_path, trust, bundle):
    trust_path = tmp_path / "trust.json"
    bundle_path = tmp_path / "bundle.json"
    out_path = tmp_path / "report.json"
    trust_path.write_text(json.dumps(trust, indent=2) + "\n")
    bundle_path.write_text(json.dumps(bundle, indent=2) + "\n")
    before_trust = trust_path.read_bytes()
    before_bundle = bundle_path.read_bytes()
    proc = subprocess.run(
        [
            str(BIN),
            "--trust",
            str(trust_path),
            "--bundle",
            str(bundle_path),
            "--out",
            str(out_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert trust_path.read_bytes() == before_trust
    assert bundle_path.read_bytes() == before_bundle
    return json.loads(out_path.read_text()), out_path.read_text()


def assert_pretty_report_contract(raw):
    """Reports use the public top-level key order, two-space indentation, and a final newline."""
    pairs = json.loads(raw, object_pairs_hook=list)
    assert [key for key, _ in pairs] == ["accepted", "rejected", "summary", "timeline"]
    assert raw == json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n"


def run_gate_under_network_trace(tmp_path, trust, bundle):
    """Run the gate under syscall tracing so attempted network access is observable."""
    trust_path = tmp_path / "trace-trust.json"
    bundle_path = tmp_path / "trace-bundle.json"
    out_path = tmp_path / "trace-report.json"
    trace_path = tmp_path / "network.trace"
    trust_path.write_text(json.dumps(trust, indent=2) + "\n")
    bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")

    proc = subprocess.run(
        [
            "strace",
            "-f",
            "-qq",
            "-e",
            "trace=network",
            "-o",
            str(trace_path),
            str(BIN),
            "--trust",
            str(trust_path),
            "--bundle",
            str(bundle_path),
            "--out",
            str(out_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    trace = trace_path.read_text()
    assert network_syscall_lines(trace) == []
    assert json.loads(out_path.read_text())["summary"]["accepted"] == 1


def healthy_bundle(key, count=5):
    entries = [entry(i) for i in range(count)]
    leaves = [leaf_hash(e["evidence"]) for e in entries]
    proofs = [
        {
            "leaf_id": e["id"],
            "log_id": "alpha",
            "tree_size": count,
            "leaf_index": e["leaf_index"],
            "audit_path": audit_path(leaves, e["leaf_index"]),
        }
        for e in entries
    ]
    return {
        "entries": entries,
        "proofs": proofs,
        "sths": [
            sth(key, "alpha", leaves[:3], 1_700_000_010),
            sth(key, "alpha", leaves, 1_700_000_020),
            sth(key, "alpha", leaves, 1_700_000_021),
        ],
        "consistency": [
            {
                "log_id": "alpha",
                "from_size": 3,
                "to_size": count,
                "leaf_hashes": [h.hex() for h in leaves],
            }
        ],
        "pins": [],
    }


def trusted_logs(*items):
    return {
        "logs": [
            {
                "log_id": log_id,
                "key": key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
            }
            for log_id, key in items
        ]
    }


def nested_unicode_bundle(key):
    """Build evidence whose leaf hash depends on nested sorted keys and UTF-8 text."""
    evidence = {
        "zeta": {"β": ["café.example", {"雪": "値", "a": 1}], "alpha": False},
        "subject": "équipe.example",
        "issuer": {"unit": "réponse", "name": "Example Offline CA"},
        "serial": "ユニコード-001",
    }
    ent = {"id": "leaf-unicode", "log_id": "alpha", "leaf_index": 0, "evidence": evidence}
    leaves = [leaf_hash(evidence)]
    return {
        "entries": [ent],
        "proofs": [
            {
                "leaf_id": ent["id"],
                "log_id": "alpha",
                "tree_size": 1,
                "leaf_index": 0,
                "audit_path": [],
            }
        ],
        "sths": [sth(key, "alpha", leaves, 1_700_000_030)],
        "consistency": [],
        "pins": [],
    }


def multi_log_pinned_bundle(alpha_key, beta_key):
    """Create two independent logs with decoys that require per-log proof, pin, and timeline handling."""
    alpha_entries = [entry(i, "alpha") for i in range(4)]
    beta_entries = [entry(i + 20, "beta") for i in range(6)]
    for i, ent in enumerate(beta_entries):
        ent["leaf_index"] = i
        ent["evidence"] = {
            "subject": f"service-{i}.zone.example",
            "issuer": {"name": "Example Offline CA", "region": "eu-west"},
            "serial": f"B-{i:03d}",
            "extensions": [{"oid": "1.3.6.1.5.5.7.1.24", "critical": False}],
        }
    alpha_leaves = [leaf_hash(e["evidence"]) for e in alpha_entries]
    beta_leaves = [leaf_hash(e["evidence"]) for e in beta_entries]
    alpha_sths = [
        sth(alpha_key, "alpha", alpha_leaves[:2], 1_700_000_010),
        sth(alpha_key, "alpha", alpha_leaves, 1_700_000_025),
    ]
    beta_sths = [
        sth(beta_key, "beta", beta_leaves[:3], 1_700_000_011),
        sth(beta_key, "beta", beta_leaves, 1_700_000_040),
    ]
    proofs = [
        {
            "leaf_id": beta_entries[5]["id"],
            "log_id": "beta",
            "tree_size": 6,
            "leaf_index": 5,
            "audit_path": audit_path(beta_leaves, 5),
        },
        {
            "leaf_id": beta_entries[1]["id"],
            "log_id": "alpha",
            "tree_size": 4,
            "leaf_index": 1,
            "audit_path": audit_path(alpha_leaves, 1),
        },
        {
            "leaf_id": alpha_entries[3]["id"],
            "log_id": "alpha",
            "tree_size": 4,
            "leaf_index": 3,
            "audit_path": audit_path(alpha_leaves, 3),
        },
    ]
    return {
        "entries": [beta_entries[5], alpha_entries[3], beta_entries[1]],
        "proofs": proofs,
        "sths": [beta_sths[0], alpha_sths[1], beta_sths[1], alpha_sths[0]],
        "consistency": [
            {
                "log_id": "alpha",
                "from_size": 2,
                "to_size": 4,
                "leaf_hashes": [h.hex() for h in alpha_leaves],
            },
            {
                "log_id": "beta",
                "from_size": 3,
                "to_size": 6,
                "leaf_hashes": [h.hex() for h in beta_leaves],
            },
        ],
        "pins": [
            {
                "log_id": "beta",
                "tree_size": 6,
                "timestamp": 1_700_000_040,
                "root_hash": tree_root(beta_leaves).hex(),
            }
        ],
    }


def witnessed_bundle(alpha_key, beta_key, gamma_key):
    """Create one primary log and two witness logs for the same evidence object."""
    primary = entry(30, "alpha")
    primary["id"] = "leaf-witnessed"
    primary["leaf_index"] = 0
    beta_copy = dict(primary, log_id="beta", leaf_index=1)
    gamma_copy = dict(primary, log_id="gamma", leaf_index=2)
    alpha_leaves = [leaf_hash(primary["evidence"])]
    beta_leaves = [leaf_hash(entry(90, "beta")["evidence"]), leaf_hash(primary["evidence"])]
    gamma_leaves = [
        leaf_hash(entry(91, "gamma")["evidence"]),
        leaf_hash(entry(92, "gamma")["evidence"]),
        leaf_hash(primary["evidence"]),
    ]
    return {
        "entries": [primary],
        "proofs": [
            {
                "leaf_id": primary["id"],
                "log_id": "alpha",
                "tree_size": 1,
                "leaf_index": 0,
                "audit_path": audit_path(alpha_leaves, 0),
            }
        ],
        "witnesses": [
            {
                "leaf_id": beta_copy["id"],
                "log_id": "beta",
                "tree_size": 2,
                "leaf_index": beta_copy["leaf_index"],
                "audit_path": audit_path(beta_leaves, beta_copy["leaf_index"]),
            },
            {
                "leaf_id": gamma_copy["id"],
                "log_id": "gamma",
                "tree_size": 3,
                "leaf_index": gamma_copy["leaf_index"],
                "audit_path": audit_path(gamma_leaves, gamma_copy["leaf_index"]),
            },
        ],
        "sths": [
            sth(alpha_key, "alpha", alpha_leaves, 1_700_000_010),
            sth(beta_key, "beta", beta_leaves, 1_700_000_011),
            sth(gamma_key, "gamma", gamma_leaves[:2], 1_700_000_012),
            sth(gamma_key, "gamma", gamma_leaves, 1_700_000_013),
        ],
        "consistency": [
            {
                "log_id": "gamma",
                "from_size": 2,
                "to_size": 3,
                "leaf_hashes": [h.hex() for h in gamma_leaves],
            }
        ],
        "pins": [],
    }


def checkpointed_bundle(log_key, checkpoint_key):
    """Create a log whose admissions depend on a matching external checkpoint."""
    entries = [entry(i) for i in range(6)]
    for i, ent in enumerate(entries):
        ent["id"] = f"checkpoint-leaf-{i}"
        ent["evidence"] = {
            "serial": f"CP-{i:03d}",
            "issuer": {"name": "Example Offline CA", "scope": "checkpointed"},
            "subject": f"checkpoint-{i}.example.test",
            "not_before": 1_700_000_000 + i,
            "not_after": 1_700_086_400 + i,
        }
    leaves = [leaf_hash(e["evidence"]) for e in entries]
    full_root = tree_root(leaves).hex()
    older_root = tree_root(leaves[:4]).hex()
    return {
        "entries": [entries[5], entries[3]],
        "proofs": [
            {
                "leaf_id": entries[5]["id"],
                "log_id": "alpha",
                "tree_size": 6,
                "leaf_index": 5,
                "audit_path": audit_path(leaves, 5),
            },
            {
                "leaf_id": entries[3]["id"],
                "log_id": "alpha",
                "tree_size": 4,
                "leaf_index": 3,
                "audit_path": audit_path(leaves[:4], 3),
            },
        ],
        "sths": [
            {
                "log_id": "alpha",
                "tree_size": 4,
                "timestamp": 1_700_000_020,
                "root_hash": older_root,
                "signature": sign_sth(log_key, "alpha", 4, older_root, 1_700_000_020),
            },
            {
                "log_id": "alpha",
                "tree_size": 6,
                "timestamp": 1_700_000_040,
                "root_hash": full_root,
                "signature": sign_sth(log_key, "alpha", 6, full_root, 1_700_000_040),
            },
        ],
        "consistency": [
            {
                "log_id": "alpha",
                "from_size": 4,
                "to_size": 6,
                "leaf_hashes": [h.hex() for h in leaves],
            }
        ],
        "checkpoints": [
            {
                "log_id": "alpha",
                "tree_size": 6,
                "timestamp": 1_700_000_041,
                "root_hash": full_root,
                "signature": sign_checkpoint(
                    checkpoint_key, "alpha", 6, full_root, 1_700_000_041
                ),
            },
            {
                "log_id": "alpha",
                "tree_size": 4,
                "timestamp": 1_700_000_019,
                "root_hash": older_root,
                "signature": sign_checkpoint(
                    checkpoint_key, "alpha", 4, older_root, 1_700_000_019
                ),
            },
        ],
        "pins": [],
    }


def test_admits_canonical_leaves_with_indexed_inclusion_and_duplicate_sths(tmp_path):
    """A healthy log with noncanonical JSON key order, right-side proof paths, and duplicate STHs is admitted deterministically."""
    build_binary()
    key = b"A" * 32
    trust = {
        "logs": [
            {
                "log_id": "alpha",
                "key": key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
            }
        ]
    }
    report, raw = run_gate(tmp_path, trust, healthy_bundle(key, 5))

    assert [a["id"] for a in report["accepted"]] == [f"leaf-{i}" for i in range(5)]
    assert report["rejected"] == []
    assert report["summary"] == {
        "accepted": 5,
        "rejected": 0,
        "malformed": 0,
        "logs": 1,
    }
    assert report["timeline"] == [
        {
            "log_id": "alpha",
            "status": "ok",
            "sth_count": 3,
            "latest_size": 5,
            "latest_timestamp": 1_700_000_021,
        }
    ]
    assert_pretty_report_contract(raw)


def test_uses_nested_utf8_canonical_json_and_never_opens_network(tmp_path):
    """Canonical leaf hashes sort nested object keys, preserve Unicode text, and do not require network syscalls."""
    build_binary()
    key = b"H" * 32
    trust = {
        "logs": [
            {
                "log_id": "alpha",
                "key": key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
            }
        ]
    }
    bundle = nested_unicode_bundle(key)
    report, raw = run_gate(tmp_path, trust, bundle)

    assert report["accepted"] == [
        {
            "id": "leaf-unicode",
            "log_id": "alpha",
            "tree_size": 1,
            "sth_timestamp": 1_700_000_030,
        }
    ]
    assert report["rejected"] == []
    assert report["summary"] == {"accepted": 1, "rejected": 0, "malformed": 0, "logs": 1}
    assert_pretty_report_contract(raw)
    run_gate_under_network_trace(tmp_path, trust, bundle)


def test_separates_multi_log_pins_decoy_proofs_and_deep_right_paths(tmp_path):
    """Pinned multi-log timelines require matching STHs while decoy cross-log proofs and deep right-side paths stay isolated."""
    build_binary()
    alpha_key = b"I" * 32
    beta_key = b"J" * 32
    trust = trusted_logs(("alpha", alpha_key), ("beta", beta_key))
    bundle = multi_log_pinned_bundle(alpha_key, beta_key)

    report, raw = run_gate(tmp_path, trust, bundle)
    assert [a["id"] for a in report["accepted"]] == ["leaf-25", "leaf-3"]
    assert report["accepted"][0] == {
        "id": "leaf-25",
        "log_id": "beta",
        "tree_size": 6,
        "sth_timestamp": 1_700_000_040,
    }
    assert report["accepted"][1] == {
        "id": "leaf-3",
        "log_id": "alpha",
        "tree_size": 4,
        "sth_timestamp": 1_700_000_025,
    }
    assert report["rejected"] == [{"id": "leaf-21", "reason": "missing-valid-proof"}]
    assert report["summary"] == {"accepted": 2, "rejected": 1, "malformed": 0, "logs": 2}
    assert report["timeline"] == [
        {
            "log_id": "alpha",
            "status": "ok",
            "sth_count": 2,
            "latest_size": 4,
            "latest_timestamp": 1_700_000_025,
        },
        {
            "log_id": "beta",
            "status": "ok",
            "sth_count": 2,
            "latest_size": 6,
            "latest_timestamp": 1_700_000_040,
        },
    ]
    assert_pretty_report_contract(raw)

    pinned_mismatch = multi_log_pinned_bundle(alpha_key, beta_key)
    pinned_mismatch["pins"][0]["root_hash"] = "00" * 32
    report, _ = run_gate(tmp_path, trust, pinned_mismatch)
    assert report["accepted"] == [
        {
            "id": "leaf-3",
            "log_id": "alpha",
            "tree_size": 4,
            "sth_timestamp": 1_700_000_025,
        }
    ]
    assert report["rejected"] == [
        {"id": "leaf-21", "reason": "bad-timeline"},
        {"id": "leaf-25", "reason": "bad-timeline"},
    ]
    assert [row["status"] for row in report["timeline"]] == ["ok", "bad"]


def test_rejects_signed_split_view_and_rollback_timelines(tmp_path):
    """Signed tree heads with incompatible roots or decreasing sizes poison the affected log instead of admitting a matching leaf."""
    build_binary()
    key = b"B" * 32
    trust = {
        "logs": [
            {
                "log_id": "alpha",
                "key": key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
            }
        ]
    }
    bundle = healthy_bundle(key, 4)
    leaves = [leaf_hash(e["evidence"]) for e in bundle["entries"]]
    forged_root = tree_root(leaves[:2]).hex()
    bundle["sths"] = [
        sth(key, "alpha", leaves[:3], 1_700_000_010),
        {
            "log_id": "alpha",
            "tree_size": 3,
            "timestamp": 1_700_000_011,
            "root_hash": forged_root,
            "signature": sign_sth(key, "alpha", 3, forged_root, 1_700_000_011),
        },
        sth(key, "alpha", leaves[:2], 1_700_000_012),
    ]
    report, _ = run_gate(tmp_path, trust, bundle)

    assert report["accepted"] == []
    assert {r["reason"] for r in report["rejected"]} == {"bad-timeline"}
    assert report["summary"]["accepted"] == 0
    assert report["timeline"][0]["status"] == "bad"


def test_requires_cross_log_witnesses_and_keeps_witness_timelines_separate(tmp_path):
    """Supplied witnesses must prove the same evidence in other trusted logs, and bad witness timelines poison the entry."""
    build_binary()
    alpha_key = b"K" * 32
    beta_key = b"L" * 32
    gamma_key = b"M" * 32
    trust = trusted_logs(("alpha", alpha_key), ("beta", beta_key), ("gamma", gamma_key))
    bundle = witnessed_bundle(alpha_key, beta_key, gamma_key)

    report, raw = run_gate(tmp_path, trust, bundle)
    assert report["accepted"] == [
        {
            "id": "leaf-witnessed",
            "log_id": "alpha",
            "tree_size": 1,
            "sth_timestamp": 1_700_000_010,
        }
    ]
    assert report["rejected"] == []
    assert [row["log_id"] for row in report["timeline"]] == ["alpha", "beta", "gamma"]
    assert [row["status"] for row in report["timeline"]] == ["ok", "ok", "ok"]
    assert_pretty_report_contract(raw)

    broken_witness = witnessed_bundle(alpha_key, beta_key, gamma_key)
    broken_witness["witnesses"][0]["audit_path"] = ["00" * 32]
    report, _ = run_gate(tmp_path, trust, broken_witness)
    assert report["accepted"] == []
    assert report["rejected"] == [
        {"id": "leaf-witnessed", "reason": "missing-valid-proof"}
    ]

    bad_witness_timeline = witnessed_bundle(alpha_key, beta_key, gamma_key)
    bad_witness_timeline["consistency"] = []
    report, _ = run_gate(tmp_path, trust, bad_witness_timeline)
    assert report["accepted"] == []
    assert report["rejected"] == [{"id": "leaf-witnessed", "reason": "bad-timeline"}]
    assert [row["status"] for row in report["timeline"]] == ["ok", "ok", "bad"]


def test_enforces_configured_witness_quorums_without_explicit_witness_hints(tmp_path):
    """A log-level witness quorum counts distinct valid witness logs and handles bad witness timelines."""
    build_binary()
    alpha_key = b"Q" * 32
    beta_key = b"R" * 32
    gamma_key = b"S" * 32
    delta_key = b"D" * 32
    trust = trusted_logs(
        ("alpha", alpha_key), ("beta", beta_key), ("gamma", gamma_key), ("delta", delta_key)
    )
    trust["logs"][0]["witness_quorum"] = {
        "logs": ["beta", "gamma", "delta"],
        "threshold": 2,
    }

    bundle = witnessed_bundle(alpha_key, beta_key, gamma_key)
    report, raw = run_gate(tmp_path, trust, bundle)
    assert report["accepted"] == [
        {
            "id": "leaf-witnessed",
            "log_id": "alpha",
            "tree_size": 1,
            "sth_timestamp": 1_700_000_010,
        }
    ]
    assert report["rejected"] == []
    assert_pretty_report_contract(raw)

    missing_configured_witness = witnessed_bundle(alpha_key, beta_key, gamma_key)
    missing_configured_witness["witnesses"] = missing_configured_witness["witnesses"][:1]
    report, _ = run_gate(tmp_path, trust, missing_configured_witness)
    assert report["accepted"] == []
    assert report["rejected"] == [
        {"id": "leaf-witnessed", "reason": "missing-valid-proof"}
    ]

    poisoned_configured_witness = witnessed_bundle(alpha_key, beta_key, gamma_key)
    poisoned_configured_witness["consistency"] = []
    report, _ = run_gate(tmp_path, trust, poisoned_configured_witness)
    assert report["accepted"] == []
    assert report["rejected"] == [{"id": "leaf-witnessed", "reason": "bad-timeline"}]


def test_requires_matching_external_checkpoints_without_poisoning_timeline(tmp_path):
    """Logs with checkpoint keys require a fresh matching checkpoint for each admitting STH."""
    build_binary()
    log_key = b"T" * 32
    checkpoint_key = b"U" * 32
    trust = {
        "logs": [
            {
                "log_id": "alpha",
                "key": log_key.hex(),
                "checkpoint_key": checkpoint_key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
            }
        ]
    }
    bundle = checkpointed_bundle(log_key, checkpoint_key)

    report, raw = run_gate(tmp_path, trust, bundle)
    assert report["accepted"] == [
        {
            "id": "checkpoint-leaf-5",
            "log_id": "alpha",
            "tree_size": 6,
            "sth_timestamp": 1_700_000_040,
        }
    ]
    assert report["rejected"] == [
        {"id": "checkpoint-leaf-3", "reason": "missing-valid-proof"}
    ]
    assert report["timeline"] == [
        {
            "log_id": "alpha",
            "status": "ok",
            "sth_count": 2,
            "latest_size": 6,
            "latest_timestamp": 1_700_000_040,
        }
    ]
    assert_pretty_report_contract(raw)

    forged_checkpoint = checkpointed_bundle(log_key, checkpoint_key)
    forged_checkpoint["checkpoints"][0]["root_hash"] = "00" * 32
    forged_checkpoint["checkpoints"][0]["signature"] = sign_checkpoint(
        checkpoint_key, "alpha", 6, "00" * 32, 1_700_000_041
    )
    report, _ = run_gate(tmp_path, trust, forged_checkpoint)
    assert report["accepted"] == []
    assert report["rejected"] == [
        {"id": "checkpoint-leaf-3", "reason": "missing-valid-proof"},
        {"id": "checkpoint-leaf-5", "reason": "missing-valid-proof"},
    ]
    assert report["timeline"][0]["status"] == "ok"


def test_requires_consistency_evidence_and_handles_malformed_bundles(tmp_path):
    """Chronological STH growth needs a valid consistency item, while malformed JSON still produces a rewardable report."""
    build_binary()
    key = b"C" * 32
    trust = {
        "logs": [
            {
                "log_id": "alpha",
                "key": key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
            }
        ]
    }
    bundle = healthy_bundle(key, 4)
    bundle["consistency"] = []
    report, _ = run_gate(tmp_path, trust, bundle)
    assert report["accepted"] == []
    assert {r["reason"] for r in report["rejected"]} == {"bad-timeline"}
    assert report["timeline"] == [
        {
            "log_id": "alpha",
            "status": "bad",
            "sth_count": 3,
            "latest_size": 4,
            "latest_timestamp": 1_700_000_021,
        }
    ]

    trust_path = tmp_path / "trust-ok.json"
    bad_bundle = tmp_path / "bad-bundle.json"
    out_path = tmp_path / "malformed-report.json"
    trust_file(trust_path, key)
    bad_bundle.write_text('{"entries": [')
    proc = subprocess.run(
        [
            str(BIN),
            "--trust",
            str(trust_path),
            "--bundle",
            str(bad_bundle),
            "--out",
            str(out_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(out_path.read_text()) == {
        "accepted": [],
        "rejected": [{"id": "", "reason": "malformed-bundle"}],
        "summary": {"accepted": 0, "rejected": 1, "malformed": 1, "logs": 0},
        "timeline": [],
    }


def test_rejects_malformed_trust_and_entry_shapes(tmp_path):
    """Malformed trust JSON and entries whose evidence is not an object are reported without admitting otherwise provable leaves."""
    build_binary()
    key = b"E" * 32
    bad_entry = entry(0)
    bad_entry["evidence"] = ["not", "an", "object"]
    blank_id = entry(1)
    blank_id["id"] = ""
    leaves = [leaf_hash(bad_entry["evidence"]), leaf_hash(blank_id["evidence"])]
    trust = {
        "logs": [
            {
                "log_id": "alpha",
                "key": key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
            }
        ]
    }
    bundle = {
        "entries": [bad_entry, blank_id],
        "proofs": [
            {
                "leaf_id": bad_entry["id"],
                "log_id": "alpha",
                "tree_size": 2,
                "leaf_index": 0,
                "audit_path": audit_path(leaves, 0),
            },
            {
                "leaf_id": "",
                "log_id": "alpha",
                "tree_size": 2,
                "leaf_index": 1,
                "audit_path": audit_path(leaves, 1),
            },
        ],
        "sths": [sth(key, "alpha", leaves, 1_700_000_020)],
        "consistency": [],
    }
    report, _ = run_gate(tmp_path, trust, bundle)
    assert report["accepted"] == []
    assert report["rejected"] == [
        {"id": "", "reason": "malformed-entry"},
        {"id": "leaf-0", "reason": "malformed-entry"},
    ]
    assert report["summary"] == {
        "accepted": 0,
        "rejected": 2,
        "malformed": 2,
        "logs": 1,
    }

    trust_path = tmp_path / "bad-trust.json"
    bundle_path = tmp_path / "bundle-ok.json"
    out_path = tmp_path / "bad-trust-report.json"
    trust_path.write_text('{"logs": [')
    bundle_path.write_text(json.dumps(healthy_bundle(key, 1), indent=2) + "\n")
    proc = subprocess.run(
        [
            str(BIN),
            "--trust",
            str(trust_path),
            "--bundle",
            str(bundle_path),
            "--out",
            str(out_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(out_path.read_text()) == {
        "accepted": [],
        "rejected": [{"id": "", "reason": "malformed-trust"}],
        "summary": {"accepted": 0, "rejected": 1, "malformed": 1, "logs": 0},
        "timeline": [],
    }


def test_bad_signatures_invalid_hex_and_proof_gaps_are_rejections(tmp_path):
    """Bad STH signatures, invalid hex material, malformed proofs, and missing proof evidence do not become admissions."""
    build_binary()
    key = b"F" * 32
    trust = {
        "logs": [
            {
                "log_id": "alpha",
                "key": key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
            }
        ]
    }
    bundle = healthy_bundle(key, 4)
    leaves = [leaf_hash(e["evidence"]) for e in bundle["entries"]]
    bundle["sths"] = [sth(key, "alpha", leaves, 1_700_000_020)]
    bundle["proofs"] = [
        {
            "leaf_id": "leaf-0",
            "log_id": "alpha",
            "tree_size": 4,
            "leaf_index": 0,
            "audit_path": ["not-hex"],
        },
        {
            "leaf_id": "leaf-2",
            "log_id": "alpha",
            "tree_size": 0,
            "leaf_index": 2,
            "audit_path": [],
        },
        {
            "leaf_id": "leaf-3",
            "log_id": "alpha",
            "tree_size": 4,
            "leaf_index": 3,
            "audit_path": ["00"],
        },
    ]
    bundle["consistency"] = []
    report, _ = run_gate(tmp_path, trust, bundle)
    assert report["accepted"] == []
    assert {r["id"] for r in report["rejected"]} == {
        "leaf-0",
        "leaf-1",
        "leaf-2",
        "leaf-3",
    }
    assert {r["reason"] for r in report["rejected"]} == {"missing-valid-proof"}
    assert report["timeline"] == [
        {
            "log_id": "alpha",
            "status": "ok",
            "sth_count": 1,
            "latest_size": 4,
            "latest_timestamp": 1_700_000_020,
        }
    ]

    consistency_bundle = healthy_bundle(key, 4)
    consistency_bundle["consistency"] = [
        {
            "log_id": "alpha",
            "from_size": 3,
            "to_size": 4,
            "leaf_hashes": ["00" * 32, "bad-hex", "00" * 32, "00" * 32],
        }
    ]
    report, _ = run_gate(tmp_path, trust, consistency_bundle)
    assert report["accepted"] == []
    assert {r["reason"] for r in report["rejected"]} == {"bad-timeline"}
    assert report["timeline"] == [
        {
            "log_id": "alpha",
            "status": "bad",
            "sth_count": 3,
            "latest_size": 4,
            "latest_timestamp": 1_700_000_021,
        }
    ]

    signed = healthy_bundle(key, 1)
    signed["sths"] = [signed["sths"][0]]
    signed["sths"][0]["signature"] = "00" * 32
    signed["sths"].append(
        {
            "log_id": "alpha",
            "tree_size": 1,
            "timestamp": 1_700_000_030,
            "root_hash": "not-hex",
            "signature": "00" * 32,
        }
    )
    report, _ = run_gate(tmp_path, trust, signed)
    assert report["accepted"] == []
    assert report["rejected"] == [{"id": "leaf-0", "reason": "missing-valid-proof"}]
    assert report["timeline"] == []


def test_sorts_rejections_and_rejects_duplicates_unknown_logs_and_stale_signatures(
    tmp_path,
):
    """Report ordering and rejection reasons remain stable when independent invalid evidence classes appear together."""
    build_binary()
    key = b"D" * 32
    trust = {
        "logs": [
            {
                "log_id": "alpha",
                "key": key.hex(),
                "valid_from": 1_700_000_000,
                "valid_until": 1_700_000_050,
            }
        ]
    }
    bundle = healthy_bundle(key, 2)
    dup = dict(bundle["entries"][0])
    dup["evidence"] = dict(dup["evidence"], serial="different")
    unknown = entry(7, "beta")
    bundle["entries"] = [unknown, bundle["entries"][1], dup, bundle["entries"][0]]
    bundle["sths"].append(
        sth(
            key,
            "alpha",
            [leaf_hash(e["evidence"]) for e in healthy_bundle(key, 1)["entries"]],
            1_800_000_001,
        )
    )
    report1, raw1 = run_gate(tmp_path, trust, bundle)
    report2, raw2 = run_gate(tmp_path, trust, bundle)

    assert raw1 == raw2
    assert [a["id"] for a in report1["accepted"]] == ["leaf-1"]
    assert report1["rejected"] == [
        {"id": "leaf-0", "reason": "duplicate-entry"},
        {"id": "leaf-7", "reason": "unknown-log"},
    ]
    assert report1["timeline"] == [
        {
            "log_id": "alpha",
            "status": "ok",
            "sth_count": 3,
            "latest_size": 2,
            "latest_timestamp": 1_700_000_021,
        }
    ]
    assert report1 == report2

    stale_only = healthy_bundle(key, 1)
    stale_only["sths"] = [
        sth(
            key,
            "alpha",
            [leaf_hash(stale_only["entries"][0]["evidence"])],
            1_800_000_001,
        )
    ]
    stale_report, _ = run_gate(tmp_path, trust, stale_only)
    assert stale_report["accepted"] == []
    assert stale_report["rejected"] == [
        {"id": "leaf-0", "reason": "missing-valid-proof"}
    ]
    assert stale_report["timeline"] == []
    assert stale_report["summary"] == {
        "accepted": 0,
        "rejected": 1,
        "malformed": 0,
        "logs": 0,
    }


def test_applies_local_trust_policy_after_timeline_classification(tmp_path):
    """Per-log issuer, lifetime, and revocation policy rejects logged evidence."""
    build_binary()
    key = b"N" * 32
    trust = {
        "logs": [
            {
                "log_id": "alpha",
                "key": key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
                "allowed_issuers": ["Example Offline CA"],
                "max_age_seconds": 31_536_000,
                "revoked_serials": ["A-003"],
            }
        ]
    }
    bundle = healthy_bundle(key, 4)
    bundle["entries"][1]["evidence"]["issuer"] = {"name": "Untrusted Lab CA"}
    bundle["entries"][2]["evidence"]["not_after"] = (
        bundle["entries"][2]["evidence"]["not_before"] + 31_536_001
    )
    leaves = [leaf_hash(e["evidence"]) for e in bundle["entries"]]
    bundle["proofs"] = [
        {
            "leaf_id": e["id"],
            "log_id": "alpha",
            "tree_size": 4,
            "leaf_index": e["leaf_index"],
            "audit_path": audit_path(leaves, e["leaf_index"]),
        }
        for e in bundle["entries"]
    ]
    bundle["sths"] = [
        sth(key, "alpha", leaves[:2], 1_700_000_010),
        sth(key, "alpha", leaves, 1_700_000_020),
    ]
    bundle["consistency"] = [
        {
            "log_id": "alpha",
            "from_size": 2,
            "to_size": 4,
            "leaf_hashes": [h.hex() for h in leaves],
        }
    ]

    report, raw = run_gate(tmp_path, trust, bundle)
    assert report["accepted"] == [
        {
            "id": "leaf-0",
            "log_id": "alpha",
            "tree_size": 4,
            "sth_timestamp": 1_700_000_020,
        }
    ]
    assert report["rejected"] == [
        {"id": "leaf-1", "reason": "policy-violation"},
        {"id": "leaf-2", "reason": "policy-violation"},
        {"id": "leaf-3", "reason": "policy-violation"},
    ]
    assert report["summary"] == {"accepted": 1, "rejected": 3, "malformed": 0, "logs": 1}
    assert report["timeline"] == [
        {
            "log_id": "alpha",
            "status": "ok",
            "sth_count": 2,
            "latest_size": 4,
            "latest_timestamp": 1_700_000_020,
        }
    ]
    assert_pretty_report_contract(raw)


def test_applies_signed_revocation_state_at_admitting_sth_time(tmp_path):
    """Signed revocation records are interpreted per evidence identity and candidate STH timestamp."""
    build_binary()
    log_key = b"V" * 32
    revocation_key = b"W" * 32
    trust = {
        "logs": [
            {
                "log_id": "alpha",
                "key": log_key.hex(),
                "revocation_key": revocation_key.hex(),
                "valid_from": 1_690_000_000,
                "valid_until": 1_800_000_000,
            }
        ]
    }
    bundle = healthy_bundle(log_key, 3)
    bundle["sths"] = [
        sth(log_key, "alpha", [leaf_hash(e["evidence"]) for e in bundle["entries"]], 1_700_000_050)
    ]
    bundle["consistency"] = []
    bundle["revocations"] = [
        {
            "issuer": "Example Offline CA",
            "serial": "A-000",
            "status": "revoked",
            "timestamp": 1_700_000_030,
            "signature": sign_revocation(
                revocation_key, "Example Offline CA", "A-000", "revoked", 1_700_000_030
            ),
        },
        {
            "issuer": "Example Offline CA",
            "serial": "A-001",
            "status": "revoked",
            "timestamp": 1_700_000_020,
            "signature": sign_revocation(
                revocation_key, "Example Offline CA", "A-001", "revoked", 1_700_000_020
            ),
        },
        {
            "issuer": "Example Offline CA",
            "serial": "A-001",
            "status": "reissued",
            "timestamp": 1_700_000_040,
            "signature": sign_revocation(
                revocation_key, "Example Offline CA", "A-001", "reissued", 1_700_000_040
            ),
        },
        {
            "issuer": "Example Offline CA",
            "serial": "A-002",
            "status": "revoked",
            "timestamp": 1_700_000_060,
            "signature": sign_revocation(
                revocation_key, "Example Offline CA", "A-002", "revoked", 1_700_000_060
            ),
        },
        {
            "issuer": "Example Offline CA",
            "serial": "A-002",
            "status": "revoked",
            "timestamp": 1_700_000_010,
            "signature": "00" * 32,
        },
    ]

    report, raw = run_gate(tmp_path, trust, bundle)
    assert report["accepted"] == [
        {
            "id": "leaf-1",
            "log_id": "alpha",
            "tree_size": 3,
            "sth_timestamp": 1_700_000_050,
        },
        {
            "id": "leaf-2",
            "log_id": "alpha",
            "tree_size": 3,
            "sth_timestamp": 1_700_000_050,
        },
    ]
    assert report["rejected"] == [{"id": "leaf-0", "reason": "policy-violation"}]
    assert report["summary"] == {"accepted": 2, "rejected": 1, "malformed": 0, "logs": 1}
    assert_pretty_report_contract(raw)


def test_argument_and_unreadable_input_errors_do_not_masquerade_as_admission(tmp_path):
    """Operational failures outside the evidence decision return nonzero instead of creating an admission report."""
    build_binary()
    missing_args = subprocess.run(
        [str(BIN), "--trust", "x"], text=True, capture_output=True, check=False
    )
    assert missing_args.returncode != 0

    out_path = tmp_path / "missing-report.json"
    missing_input = subprocess.run(
        [
            str(BIN),
            "--trust",
            str(tmp_path / "absent-trust.json"),
            "--bundle",
            str(tmp_path / "absent-bundle.json"),
            "--out",
            str(out_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing_input.returncode != 0
    assert not out_path.exists()

    key = b"G" * 32
    trust_path = tmp_path / "trust.json"
    bundle_path = tmp_path / "bundle.json"
    trust_file(trust_path, key)
    bundle_path.write_text(json.dumps(healthy_bundle(key, 1), indent=2) + "\n")
    write_to_directory = subprocess.run(
        [
            str(BIN),
            "--trust",
            str(trust_path),
            "--bundle",
            str(bundle_path),
            "--out",
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert write_to_directory.returncode != 0
