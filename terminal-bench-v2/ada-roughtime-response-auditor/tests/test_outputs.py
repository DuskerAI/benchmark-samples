import hashlib
import json
import subprocess
from pathlib import Path

import pytest

APP = Path("/app/rtgate")
BIN = APP / "bin" / "rtgate"


@pytest.fixture(scope="session", autouse=True)
def build_binary():
    """Build the auditor before behavioral checks run."""
    subprocess.run([str(APP / "scripts" / "build.sh")], check=True, cwd=APP)
    assert BIN.exists()
    assert BIN.read_bytes()[:4] == b"\x7fELF"


@pytest.fixture()
def case_dir(tmp_path):
    """Create a fresh evidence workspace for one verifier case."""
    return tmp_path


class EdKey:
    def __init__(self, directory: Path, name: str):
        self.private = directory / f"{name}.pem"
        self.public_der = directory / f"{name}.der"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(self.private)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["openssl", "pkey", "-in", str(self.private), "-pubout", "-outform", "DER", "-out", str(self.public_der)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        der = self.public_der.read_bytes()
        self.pubhex = der[-32:].hex()

    def sign(self, message: bytes) -> str:
        msg = self.private.with_suffix(".msg")
        sig = self.private.with_suffix(".sig")
        msg.write_bytes(message)
        subprocess.run(
            ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(self.private), "-in", str(msg), "-out", str(sig)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return sig.read_bytes().hex()


def leaf_hash(nonce_hex: str) -> str:
    """Return the documented leaf hash for a nonce."""
    return hashlib.sha256(b"RTE-LEAF\x00" + bytes.fromhex(nonce_hex)).hexdigest()


def node_hash(left_hex: str, right_hex: str) -> str:
    """Return the documented parent hash for two children."""
    return hashlib.sha256(b"RTE-NODE\x00" + bytes.fromhex(left_hex) + bytes.fromhex(right_hex)).hexdigest()


def merkle_path(nonces, index):
    """Create a power-of-two Merkle proof for one nonce."""
    level = [leaf_hash(n) for n in nonces]
    idx = index
    tokens = []
    while len(level) > 1:
        sibling = idx ^ 1
        if idx % 2 == 0:
            tokens.append("R:" + level[sibling])
        else:
            tokens.append("L:" + level[sibling])
        nxt = [node_hash(level[pos], level[pos + 1]) for pos in range(0, len(level), 2)]
        level = nxt
        idx //= 2
    return " ".join(tokens), level[0]


def delegation_message(server, delegate_pub, start, end):
    """Return the canonical delegation transcript."""
    return f"RTE-DELEGATION\n{server}\n{delegate_pub}\n{start}\n{end}\n".encode()


def response_message(server, nonce, root_hash, midpoint, radius):
    """Return the canonical response transcript."""
    return f"RTE-RESPONSE\n{server}\n{nonce}\n{root_hash}\n{midpoint}\n{radius}\n".encode()


def chain_hash(prev_hash, server, nonce, root_hash, midpoint, radius):
    """Return the documented per-server admission-chain digest."""
    payload = (
        b"RTE-CHAIN\x00"
        + bytes.fromhex(prev_hash)
        + server.encode()
        + b"\x00"
        + bytes.fromhex(nonce)
        + bytes.fromhex(root_hash)
        + str(midpoint).encode()
        + b"\x00"
        + str(radius).encode()
    )
    return hashlib.sha256(payload).hexdigest()


def write_evidence(
    path: Path,
    *,
    evid="alpha",
    server="time-a",
    root_name="main",
    root_key,
    delegate_key,
    start=900,
    end=1200,
    nonce=None,
    midpoint=1000,
    radius=25,
    nonces=None,
    index=0,
    prev_hash="00" * 32,
    mutate=None,
):
    """Write one signed evidence file with optional field changes."""
    if nonce is None:
        nonce = hashlib.sha256(evid.encode()).hexdigest()
    if nonces is None:
        nonces = [nonce]
    path_text, root_hash = merkle_path(nonces, index)
    fields = {
        "id": evid,
        "server": server,
        "root": root_name,
        "delegation-start": str(start),
        "delegation-end": str(end),
        "delegate-pub": delegate_key.pubhex,
        "nonce": nonce,
        "midpoint": str(midpoint),
        "radius": str(radius),
        "index": str(index),
        "leaves": str(len(nonces)),
        "path": path_text,
        "root-hash": root_hash,
        "prev-hash": prev_hash,
    }
    fields["chain-hash"] = chain_hash(prev_hash, server, nonce, root_hash, midpoint, radius)
    fields["delegate-sig"] = root_key.sign(
        delegation_message(server, fields["delegate-pub"], fields["delegation-start"], fields["delegation-end"])
    )
    fields["response-sig"] = delegate_key.sign(
        response_message(server, fields["nonce"], fields["root-hash"], fields["midpoint"], fields["radius"])
    )
    extra_lines = []
    if mutate:
        mutate(fields, extra_lines)
    order = [
        "id",
        "server",
        "root",
        "delegation-start",
        "delegation-end",
        "delegate-pub",
        "delegate-sig",
        "nonce",
        "midpoint",
        "radius",
        "index",
        "leaves",
        "path",
        "root-hash",
        "prev-hash",
        "chain-hash",
        "response-sig",
    ]
    lines = ["RTE1", *[f"{key}: {fields[key]}" for key in order], *extra_lines]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return fields


def run_audit(work: Path, trust_text: str, now=1000):
    """Run the auditor and return the JSON report."""
    trust = work / "trust.txt"
    evidence = work / "evidence"
    out = work / "report.json"
    evidence.mkdir(exist_ok=True)
    trust.write_text(trust_text)
    completed = subprocess.run(
        [str(BIN), "--trust", str(trust), "--evidence", str(evidence), "--now", str(now), "--out", str(out)],
        cwd=APP,
        text=True,
        capture_output=True,
        check=True,
    )
    assert completed.stdout == ""
    return json.loads(out.read_text())


def keypair_dir(work: Path, label="keys"):
    """Create trusted root and delegated signing keys."""
    keys = work / label
    keys.mkdir()
    root = EdKey(keys, "root")
    delegated = EdKey(keys, "delegated")
    return root, delegated


def test_admits_only_fully_valid_signed_evidence(case_dir):
    """A valid response is admitted and a response with a bad signature is rejected."""
    root, delegated = keypair_dir(case_dir)
    evidence = case_dir / "evidence"
    evidence.mkdir()
    write_evidence(evidence / "b-valid.rte", evid="valid-2", root_key=root, delegate_key=delegated)

    def alter(fields, _extra):
        fields["response-sig"] = "00" * 64

    write_evidence(evidence / "a-bad.rte", evid="bad-1", root_key=root, delegate_key=delegated, mutate=alter)
    report = run_audit(case_dir, f"root main {root.pubhex}\n")
    assert report == {
        "accepted": [{"id": "valid-2", "source": "b-valid.rte", "server": "time-a", "midpoint": 1000, "radius": 25}],
        "rejected": [{"id": "bad-1", "source": "a-bad.rte", "reason": "bad-response"}],
        "summary": {"accepted": 1, "rejected": 1},
    }


def test_merkle_direction_and_nonce_membership_are_enforced(case_dir):
    """Merkle proofs must bind the signed nonce to the documented root and directions."""
    root, delegated = keypair_dir(case_dir)
    evidence = case_dir / "evidence"
    evidence.mkdir()
    nonce_a = "11" * 32
    nonce_b = "22" * 32
    write_evidence(evidence / "ok.rte", evid="ok", root_key=root, delegate_key=delegated, nonce=nonce_a, nonces=[nonce_a, nonce_b], index=0)

    def wrong_direction(fields, _extra):
        fields["path"] = fields["path"].replace("R:", "L:", 1)
        fields["response-sig"] = delegated.sign(
            response_message(fields["server"], fields["nonce"], fields["root-hash"], fields["midpoint"], fields["radius"])
        )

    write_evidence(evidence / "bad.rte", evid="bad", root_key=root, delegate_key=delegated, nonce=nonce_a, nonces=[nonce_a, nonce_b], index=0, mutate=wrong_direction)
    report = run_audit(case_dir, f"root main {root.pubhex}\n")
    assert report["accepted"] == [{"id": "ok", "source": "ok.rte", "server": "time-a", "midpoint": 1000, "radius": 25}]
    assert report["rejected"] == [{"id": "bad", "source": "bad.rte", "reason": "bad-merkle"}]
    assert report["summary"] == {"accepted": 1, "rejected": 1}


def test_delegation_and_freshness_windows_are_independent(case_dir):
    """Delegation coverage, signatures, and midpoint-radius freshness are enforced independently."""
    root, delegated = keypair_dir(case_dir)
    evidence = case_dir / "evidence"
    evidence.mkdir()
    write_evidence(evidence / "deleg.rte", evid="deleg", root_key=root, delegate_key=delegated, start=1010, end=1100, midpoint=1000, radius=50)

    def forged_delegation(fields, _extra):
        fields["delegate-sig"] = "00" * 64

    write_evidence(evidence / "forged.rte", evid="forged", root_key=root, delegate_key=delegated, mutate=forged_delegation)
    write_evidence(evidence / "stale.rte", evid="stale", root_key=root, delegate_key=delegated, start=900, end=1200, midpoint=1000, radius=5)
    report = run_audit(case_dir, f"root main {root.pubhex}\n", now=1008)
    assert report == {
        "accepted": [],
        "rejected": [
            {"id": "deleg", "source": "deleg.rte", "reason": "bad-delegation"},
            {"id": "forged", "source": "forged.rte", "reason": "bad-delegation"},
            {"id": "stale", "source": "stale.rte", "reason": "stale"},
        ],
        "summary": {"accepted": 0, "rejected": 3},
    }


def test_duplicate_ids_are_not_admitted(case_dir):
    """Every member of a repeated evidence id group is rejected."""
    root, delegated = keypair_dir(case_dir)
    evidence = case_dir / "evidence"
    evidence.mkdir()
    write_evidence(evidence / "one.rte", evid="same", root_key=root, delegate_key=delegated, nonce="33" * 32)
    write_evidence(evidence / "two.rte", evid="same", root_key=root, delegate_key=delegated, nonce="44" * 32)
    report = run_audit(case_dir, f"root main {root.pubhex}\n")
    assert report == {
        "accepted": [],
        "rejected": [
            {"id": "same", "source": "one.rte", "reason": "duplicate"},
            {"id": "same", "source": "two.rte", "reason": "duplicate"},
        ],
        "summary": {"accepted": 0, "rejected": 2},
    }


def test_server_chain_state_is_bound_and_ordered(case_dir):
    """Accepted responses for a server must preserve the public admission-chain state."""
    root, delegated = keypair_dir(case_dir)
    evidence = case_dir / "evidence"
    evidence.mkdir()
    first = write_evidence(evidence / "second-name.rte", evid="a-first", root_key=root, delegate_key=delegated, nonce="55" * 32, midpoint=990, radius=30)
    write_evidence(
        evidence / "first-name.rte",
        evid="b-second",
        root_key=root,
        delegate_key=delegated,
        nonce="66" * 32,
        midpoint=1000,
        radius=30,
        prev_hash=first["chain-hash"],
    )
    write_evidence(
        evidence / "third.rte",
        evid="c-third",
        root_key=root,
        delegate_key=delegated,
        nonce="77" * 32,
        midpoint=1010,
        radius=30,
        prev_hash=first["chain-hash"],
    )
    report = run_audit(case_dir, f"root main {root.pubhex}\n", now=1000)
    assert report["accepted"] == [
        {"id": "a-first", "source": "second-name.rte", "server": "time-a", "midpoint": 990, "radius": 30},
        {"id": "b-second", "source": "first-name.rte", "server": "time-a", "midpoint": 1000, "radius": 30},
    ]
    assert report["rejected"] == [{"id": "c-third", "source": "third.rte", "reason": "bad-chain"}]
    assert report["summary"] == {"accepted": 2, "rejected": 1}


def test_canonical_parsing_and_unknown_roots_are_reported(case_dir):
    """Malformed canonical text and untrusted root names produce deterministic rejection reasons."""
    root, delegated = keypair_dir(case_dir)
    evidence = case_dir / "evidence"
    evidence.mkdir()

    def duplicate_header(fields, extra):
        extra.append("nonce: " + fields["nonce"])

    write_evidence(evidence / "malformed.rte", evid="canon", root_key=root, delegate_key=delegated, mutate=duplicate_header)
    write_evidence(evidence / "unknown.rte", evid="unknown", root_name="other", root_key=root, delegate_key=delegated)
    report = run_audit(case_dir, f"root main {root.pubhex}\n")
    assert report == {
        "accepted": [],
        "rejected": [
            {"id": "canon", "source": "malformed.rte", "reason": "malformed"},
            {"id": "unknown", "source": "unknown.rte", "reason": "unknown-root"},
        ],
        "summary": {"accepted": 0, "rejected": 2},
    }


def test_duplicate_precedence_and_malformed_id_preservation_interact(case_dir):
    """Duplicate ids dominate later validation reasons while malformed files keep valid ids."""
    root, delegated = keypair_dir(case_dir)
    evidence = case_dir / "evidence"
    evidence.mkdir()

    def unknown_later(fields, _extra):
        fields["root"] = "missing"

    def malformed_after_id(fields, extra):
        extra.append("delegate-pub: " + fields["delegate-pub"])

    write_evidence(evidence / "a-good-root.rte", evid="dupe", root_key=root, delegate_key=delegated, nonce="88" * 32)
    write_evidence(
        evidence / "b-unknown-root.rte",
        evid="dupe",
        root_key=root,
        delegate_key=delegated,
        nonce="99" * 32,
        mutate=unknown_later,
    )
    write_evidence(evidence / "c-malformed.rte", evid="kept", root_key=root, delegate_key=delegated, mutate=malformed_after_id)
    report = run_audit(case_dir, f"root main {root.pubhex}\n")
    assert report == {
        "accepted": [],
        "rejected": [
            {"id": "dupe", "source": "a-good-root.rte", "reason": "duplicate"},
            {"id": "dupe", "source": "b-unknown-root.rte", "reason": "duplicate"},
            {"id": "kept", "source": "c-malformed.rte", "reason": "malformed"},
        ],
        "summary": {"accepted": 0, "rejected": 3},
    }


def test_chain_state_isolated_by_server_and_rejection(case_dir):
    """Admission chains are per-server and rejected candidates do not advance state."""
    root, delegated = keypair_dir(case_dir)
    evidence = case_dir / "evidence"
    evidence.mkdir()
    alpha_one = write_evidence(
        evidence / "alpha-1.rte",
        evid="alpha-1",
        server="alpha",
        root_key=root,
        delegate_key=delegated,
        nonce="aa" * 32,
        midpoint=980,
        radius=40,
    )
    write_evidence(
        evidence / "beta-1.rte",
        evid="beta-1",
        server="beta",
        root_key=root,
        delegate_key=delegated,
        nonce="bb" * 32,
        midpoint=985,
        radius=40,
    )
    write_evidence(
        evidence / "alpha-gap.rte",
        evid="alpha-gap",
        server="alpha",
        root_key=root,
        delegate_key=delegated,
        nonce="cc" * 32,
        midpoint=990,
        radius=40,
        prev_hash="12" * 32,
    )
    write_evidence(
        evidence / "alpha-2.rte",
        evid="alpha-2",
        server="alpha",
        root_key=root,
        delegate_key=delegated,
        nonce="dd" * 32,
        midpoint=995,
        radius=40,
        prev_hash=alpha_one["chain-hash"],
    )
    report = run_audit(case_dir, f"root main {root.pubhex}\n", now=1000)
    assert report["accepted"] == [
        {"id": "alpha-1", "source": "alpha-1.rte", "server": "alpha", "midpoint": 980, "radius": 40},
        {"id": "alpha-2", "source": "alpha-2.rte", "server": "alpha", "midpoint": 995, "radius": 40},
        {"id": "beta-1", "source": "beta-1.rte", "server": "beta", "midpoint": 985, "radius": 40},
    ]
    assert report["rejected"] == [{"id": "alpha-gap", "source": "alpha-gap.rte", "reason": "bad-chain"}]
    assert report["summary"] == {"accepted": 3, "rejected": 1}


def test_server_nonce_replay_is_a_chain_violation(case_dir):
    """A server admission chain rejects nonce replay without poisoning other servers."""
    root, delegated = keypair_dir(case_dir)
    evidence = case_dir / "evidence"
    evidence.mkdir()
    first = write_evidence(
        evidence / "alpha-first.rte",
        evid="alpha-first",
        server="alpha",
        root_key=root,
        delegate_key=delegated,
        nonce="12" * 32,
        midpoint=980,
        radius=60,
    )
    write_evidence(
        evidence / "alpha-replay.rte",
        evid="alpha-replay",
        server="alpha",
        root_key=root,
        delegate_key=delegated,
        nonce="12" * 32,
        midpoint=990,
        radius=60,
        prev_hash=first["chain-hash"],
    )
    second = write_evidence(
        evidence / "alpha-second.rte",
        evid="alpha-second",
        server="alpha",
        root_key=root,
        delegate_key=delegated,
        nonce="34" * 32,
        midpoint=1000,
        radius=60,
        prev_hash=first["chain-hash"],
    )
    write_evidence(
        evidence / "alpha-third.rte",
        evid="alpha-third",
        server="alpha",
        root_key=root,
        delegate_key=delegated,
        nonce="56" * 32,
        midpoint=1010,
        radius=60,
        prev_hash=second["chain-hash"],
    )
    write_evidence(
        evidence / "beta-same-nonce.rte",
        evid="beta-same",
        server="beta",
        root_key=root,
        delegate_key=delegated,
        nonce="12" * 32,
        midpoint=985,
        radius=60,
    )
    report = run_audit(case_dir, f"root main {root.pubhex}\n", now=1000)
    assert report["accepted"] == [
        {"id": "alpha-first", "source": "alpha-first.rte", "server": "alpha", "midpoint": 980, "radius": 60},
        {"id": "alpha-second", "source": "alpha-second.rte", "server": "alpha", "midpoint": 1000, "radius": 60},
        {"id": "alpha-third", "source": "alpha-third.rte", "server": "alpha", "midpoint": 1010, "radius": 60},
        {"id": "beta-same", "source": "beta-same-nonce.rte", "server": "beta", "midpoint": 985, "radius": 60},
    ]
    assert report["rejected"] == [{"id": "alpha-replay", "source": "alpha-replay.rte", "reason": "bad-chain"}]
    assert report["summary"] == {"accepted": 4, "rejected": 1}


def test_trust_replacement_and_revocation_do_not_advance_chain(case_dir):
    """Later trust roots replace earlier keys, and revoked delegations do not advance chain state."""
    stale_root, _ = keypair_dir(case_dir, "stale-keys")
    root, delegated = keypair_dir(case_dir, "active-keys")
    evidence = case_dir / "evidence"
    evidence.mkdir()
    revoked = write_evidence(
        evidence / "a-revoked.rte",
        evid="revoked",
        server="alpha",
        root_key=root,
        delegate_key=delegated,
        nonce="ab" * 32,
        midpoint=1000,
        radius=50,
    )
    write_evidence(
        evidence / "b-live.rte",
        evid="live",
        server="alpha",
        root_key=root,
        delegate_key=delegated,
        nonce="cd" * 32,
        midpoint=1010,
        radius=50,
    )
    write_evidence(
        evidence / "c-wrong-prev.rte",
        evid="wrong-prev",
        server="alpha",
        root_key=root,
        delegate_key=delegated,
        nonce="ef" * 32,
        midpoint=1020,
        radius=50,
        prev_hash=revoked["chain-hash"],
    )
    trust = "\n".join(
        [
            f"root main {stale_root.pubhex}",
            f"root main {root.pubhex}",
            "revoke main alpha 995 1005",
            "",
        ]
    )
    report = run_audit(case_dir, trust, now=1010)
    assert report == {
        "accepted": [{"id": "live", "source": "b-live.rte", "server": "alpha", "midpoint": 1010, "radius": 50}],
        "rejected": [
            {"id": "revoked", "source": "a-revoked.rte", "reason": "bad-delegation"},
            {"id": "wrong-prev", "source": "c-wrong-prev.rte", "reason": "bad-chain"},
        ],
        "summary": {"accepted": 1, "rejected": 2},
    }
