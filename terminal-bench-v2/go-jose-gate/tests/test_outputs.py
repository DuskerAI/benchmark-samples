"""Behavioral verifier for the offline JOSE admission gate."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

APP = Path("/app/josegate")
BIN = APP / "bin" / "josegate"
NOW = 1_700_000_500
WORK = Path("/tmp/josegate-verifier")
RUN_UID = 65534
RUN_GID = 65534
CANDIDATE_ENV = {
    **os.environ,
    "HOME": "/tmp/josegate-candidate-home",
    "GOCACHE": "/tmp/josegate-candidate-home/go-build-cache",
}
B64URL_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
HEX_DIGITS = set("0123456789abcdef")


def b64(data: bytes) -> str:
    """Encode bytes as unpadded base64url."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    """Decode strict unpadded base64url text."""
    if "=" in text or any(ch not in B64URL_CHARS for ch in text):
        raise ValueError("not unpadded base64url")
    padded = text + "=" * ((4 - len(text) % 4) % 4)
    return base64.b64decode(padded, altchars=b"-_", validate=True)


def canonical(obj: object) -> bytes:
    """Serialize JSON with the public canonical form used by the contract."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def digest(data: bytes) -> str:
    """Return the lowercase SHA-256 digest used by evidence claims."""
    return hashlib.sha256(data).hexdigest()


def segments_for(data: bytes, split: int | None = None) -> list[dict[str, object]]:
    """Build contiguous segment claims for artifact bytes."""
    if not data:
        return [{"offset": 0, "length": 1, "sha256": digest(b"x")}]
    split_at = split or max(1, len(data) // 2)
    spans = [(0, split_at), (split_at, len(data))] if split_at < len(data) else [(0, len(data))]
    return [
        {
            "offset": start,
            "length": end - start,
            "sha256": digest(data[start:end]),
        }
        for start, end in spans
    ]


def make_claim(
    *,
    evidence_id: str,
    subject: str,
    nonce: str,
    seq: int,
    artifact: str,
    artifact_bytes: bytes,
    previous: str | None,
    nbf: int = NOW - 30,
    exp: int = NOW + 30,
    event_time: int = NOW,
    sha256: str | None = None,
    artifact_size: int | None = None,
    segments: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Create a payload claim that follows the public schema by default."""
    return {
        "evidence_id": evidence_id,
        "subject": subject,
        "nonce": nonce,
        "artifact": artifact,
        "sha256": sha256 if sha256 is not None else digest(artifact_bytes),
        "event_time": event_time,
        "nbf": nbf,
        "exp": exp,
        "seq": seq,
        "previous": previous,
        "artifact_size": len(artifact_bytes) if artifact_size is None else artifact_size,
        "segments": segments if segments is not None else segments_for(artifact_bytes),
    }


def make_token(
    token_id: str,
    kid: str,
    key: bytes,
    claim: dict[str, object],
    *,
    header_override: bytes | None = None,
    payload_override: bytes | None = None,
    signature_key: bytes | None = None,
    corrupt_signature: bool = False,
) -> dict[str, str]:
    """Create a detached-style JWS token for generated verifier dossiers."""
    header = {
        "alg": "HS256",
        "kid": kid,
        "typ": "evidence+jws",
        "cty": "application/josegate+json",
        "crit": ["josegate-c14n"],
    }
    protected = b64(header_override if header_override is not None else canonical(header))
    payload = b64(payload_override if payload_override is not None else canonical(claim))
    signing_input = f"{protected}.{payload}".encode()
    sig = hmac.new(signature_key or key, signing_input, hashlib.sha256).digest()
    if corrupt_signature:
        sig = bytes([sig[0] ^ 1]) + sig[1:]
    return {
        "id": token_id,
        "protected": protected,
        "payload": payload,
        "signature": b64(sig),
    }


def write_json(path: Path, obj: object) -> None:
    """Write JSON fixture data with deterministic formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_gate() -> None:
    """Build the gate as an unprivileged candidate without verifier-file access."""
    protect_verifier_sources()
    prepare_candidate_writable_paths(APP / "bin", APP / "out", Path(CANDIDATE_ENV["HOME"]))
    subprocess.run(
        [str(APP / "scripts" / "build.sh")],
        check=True,
        cwd=APP,
        env=CANDIDATE_ENV,
        preexec_fn=drop_to_candidate_user,
    )
    assert BIN.exists(), "build script did not create the documented binary"


def drop_to_candidate_user() -> None:
    """Demote a subprocess to the unprivileged user used for candidate code."""
    os.setgid(RUN_GID)
    os.setuid(RUN_UID)


def prepare_candidate_writable_paths(*paths: Path) -> None:
    """Create and chown only the directories the candidate must write."""
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        for item in [path, *path.rglob("*")]:
            os.chown(item, RUN_UID, RUN_GID)


def protect_verifier_sources() -> None:
    """Keep /tests readable by pytest's root verifier, not by candidate code."""
    tests_dir = Path("/tests")
    if tests_dir.exists():
        os.chown(tests_dir, 0, 0)
        os.chmod(tests_dir, 0o700)
        probe = subprocess.run(
            [
                "python3",
                "-c",
                "from pathlib import Path\ntry:\n    visible = Path('/tests/test_outputs.py').exists()\nexcept PermissionError:\n    visible = False\nraise SystemExit(1 if visible else 0)",
            ],
            check=False,
            preexec_fn=drop_to_candidate_user,
        )
        assert probe.returncode == 0, "candidate user can read verifier sources"


def run_gate(
    keys: Path,
    tokens: Path,
    state: Path,
    out: Path,
    now: int = NOW,
) -> dict[str, object]:
    """Run josegate as the unprivileged candidate and return its parsed report."""
    protect_verifier_sources()
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, RUN_UID, RUN_GID)
    result = subprocess.run(
        [
            str(BIN),
            "--keys",
            str(keys),
            "--tokens",
            str(tokens),
            "--state",
            str(state),
            "--out",
            str(out),
            "--now",
            str(now),
        ],
        cwd=APP,
        env=CANDIDATE_ENV,
        preexec_fn=drop_to_candidate_user,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def file_digest(path: Path) -> str:
    """Hash an input file so tests can detect evidence rewrites."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_int(value: object) -> bool:
    """Return whether a JSON value is an integer but not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_hex64(value: object) -> bool:
    """Return whether a JSON value is a lowercase SHA-256 hex digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in HEX_DIGITS for ch in value)
    )


def usable_keys(keys_path: Path) -> dict[str, tuple[bytes, int, int]]:
    """Load only JWKs that are valid signing keys under the public rules."""
    raw = json.loads(keys_path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for key in raw["keys"]:
        counts[key.get("kid", "")] = counts.get(key.get("kid", ""), 0) + 1
    keys: dict[str, tuple[bytes, int, int]] = {}
    for key in raw["keys"]:
        kid = key.get("kid")
        nbf = key.get("nbf")
        exp = key.get("exp")
        if (
            isinstance(kid, str)
            and counts[kid] == 1
            and key.get("kty") == "oct"
            and key.get("alg") == "HS256"
            and key.get("use") == "sig"
            and is_int(nbf)
            and is_int(exp)
            and nbf <= exp
        ):
            try:
                keys[kid] = (b64d(key["k"]), nbf, exp)
            except (binascii.Error, ValueError):
                continue
    return keys


def load_state(state_path: Path) -> tuple[set[tuple[str, str]], dict[str, tuple[int, str]]]:
    """Read replay state into nonce and subject-chain memories."""
    state = json.loads(state_path.read_text(encoding="utf-8"))
    nonces: set[tuple[str, str]] = set()
    subjects: dict[str, tuple[int, str]] = {}
    for rec in state.get("accepted", []):
        nonces.add((rec["kid"], rec["nonce"]))
        subject = rec["subject"]
        current = subjects.get(subject)
        if current is None or rec["seq"] > current[0]:
            subjects[subject] = (rec["seq"], rec["sha256"])
    return nonces, subjects


def header_is_exact(header: object) -> bool:
    """Check the exact protected header required by the contract."""
    return (
        isinstance(header, dict)
        and set(header) == {"alg", "kid", "typ", "cty", "crit"}
        and header.get("alg") == "HS256"
        and isinstance(header.get("kid"), str)
        and header.get("typ") == "evidence+jws"
        and header.get("cty") == "application/josegate+json"
        and header.get("crit") == ["josegate-c14n"]
    )


def validate_claim_shape(claim: object, token_id: str) -> bool:
    """Check claim types and fixed lexical forms required by the contract."""
    if not isinstance(claim, dict) or claim.get("evidence_id") != token_id:
        return False
    for name in ["evidence_id", "subject", "nonce", "artifact"]:
        if not isinstance(claim.get(name), str):
            return False
    for name in ["event_time", "nbf", "exp", "seq", "artifact_size"]:
        if not is_int(claim.get(name)):
            return False
    previous = claim.get("previous")
    if previous is not None and not is_hex64(previous):
        return False
    if not is_hex64(claim.get("sha256")):
        return False
    segments = claim.get("segments")
    if not isinstance(segments, list) or not segments:
        return False
    for segment in segments:
        if not isinstance(segment, dict) or set(segment) != {"offset", "length", "sha256"}:
            return False
        if not is_int(segment["offset"]) or not is_int(segment["length"]):
            return False
        if not is_hex64(segment["sha256"]):
            return False
    return True


def valid_artifact_path(name: str) -> bool:
    """Apply the public relative-path rules for artifact binding."""
    if not name or name.startswith("/") or "\\" in name:
        return False
    parts = name.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def segments_match(data: bytes, claim: dict[str, Any]) -> bool:
    """Validate artifact length, coverage, and per-segment digests."""
    if claim["artifact_size"] != len(data):
        return False
    offset = 0
    for segment in claim["segments"]:
        if segment["offset"] != offset or segment["length"] <= 0:
            return False
        end = offset + segment["length"]
        if end > len(data):
            return False
        if digest(data[offset:end]) != segment["sha256"]:
            return False
        offset = end
    return offset == len(data)


def expected_report(
    keys_path: Path,
    tokens_dir: Path,
    state_path: Path,
    now: int = NOW,
) -> dict[str, object]:
    """Independently compute the report promised by the public contract."""
    keys = usable_keys(keys_path)
    artifacts_root = tokens_dir.parent / "artifacts"
    nonces, subjects = load_state(state_path)
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, str]] = []
    token_paths = sorted(tokens_dir.glob("*.json"), key=lambda p: os.fsencode(str(p)))
    for path in token_paths:
        token_id = ""
        try:
            token = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            rejected.append({"id": token_id, "reason": "malformed"})
            continue
        raw_id = token.get("id", "") if isinstance(token, dict) else ""
        token_id = raw_id if isinstance(raw_id, str) else ""
        fields_ok = isinstance(raw_id, str) and isinstance(token, dict) and all(
            isinstance(token.get(k), str)
            for k in ["protected", "payload", "signature"]
        )
        if not fields_ok:
            rejected.append({"id": token_id, "reason": "malformed"})
            continue
        try:
            header_bytes = b64d(token["protected"])
            payload_bytes = b64d(token["payload"])
            signature = b64d(token["signature"])
            if b64(signature) != token["signature"] or len(signature) != 32:
                raise ValueError("signature encoding")
        except (binascii.Error, ValueError):
            rejected.append({"id": token_id, "reason": "bad_encoding"})
            continue
        try:
            header = json.loads(header_bytes)
            claim = json.loads(payload_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            rejected.append({"id": token_id, "reason": "malformed"})
            continue
        if b64(canonical(header)) != token["protected"]:
            rejected.append({"id": token_id, "reason": "noncanonical"})
            continue
        if b64(canonical(claim)) != token["payload"]:
            rejected.append({"id": token_id, "reason": "noncanonical"})
            continue
        if not header_is_exact(header):
            rejected.append({"id": token_id, "reason": "bad_header"})
            continue
        kid = header["kid"]
        if kid not in keys:
            rejected.append({"id": token_id, "reason": "unknown_kid"})
            continue
        key_bytes, key_nbf, key_exp = keys[kid]
        mac = hmac.new(
            key_bytes,
            f"{token['protected']}.{token['payload']}".encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(mac, signature):
            rejected.append({"id": token_id, "reason": "bad_signature"})
            continue
        if not validate_claim_shape(claim, token_id):
            rejected.append({"id": token_id, "reason": "malformed"})
            continue
        if not (
            claim["nbf"] <= now <= claim["exp"]
            and claim["nbf"] <= claim["event_time"] <= claim["exp"]
            and claim["exp"] - claim["nbf"] <= 86_400
        ):
            rejected.append({"id": token_id, "reason": "claim_window"})
            continue
        if not (key_nbf <= claim["event_time"] <= key_exp):
            rejected.append({"id": token_id, "reason": "key_window"})
            continue
        artifact = claim["artifact"]
        if not valid_artifact_path(artifact):
            rejected.append({"id": token_id, "reason": "artifact_path"})
            continue
        try:
            artifact_bytes = (artifacts_root / artifact).read_bytes()
        except OSError:
            rejected.append({"id": token_id, "reason": "artifact_path"})
            continue
        if digest(artifact_bytes) != claim["sha256"]:
            rejected.append({"id": token_id, "reason": "artifact_digest"})
            continue
        if not segments_match(artifact_bytes, claim):
            rejected.append({"id": token_id, "reason": "artifact_digest"})
            continue
        nonce_key = (kid, claim["nonce"])
        if nonce_key in nonces:
            rejected.append({"id": token_id, "reason": "replay"})
            continue
        prior = subjects.get(claim["subject"])
        if prior is not None and claim["seq"] <= prior[0]:
            rejected.append({"id": token_id, "reason": "sequence"})
            continue
        required_previous = None if prior is None else prior[1]
        if claim["previous"] != required_previous:
            rejected.append({"id": token_id, "reason": "chain"})
            continue
        row = {
            "id": token_id,
            "subject": claim["subject"],
            "kid": kid,
            "seq": claim["seq"],
            "sha256": claim["sha256"],
        }
        accepted.append(row)
        nonces.add(nonce_key)
        subjects[claim["subject"]] = (claim["seq"], claim["sha256"])
    return {
        "accepted": accepted,
        "rejected": rejected,
        "summary": {"accepted": len(accepted), "rejected": len(rejected)},
    }


def make_case(
    root: Path,
    tokens: list[dict[str, str]],
    keys: list[dict[str, object]],
    state: dict[str, object],
    artifacts: dict[str, bytes] | None = None,
) -> tuple[Path, Path, Path]:
    """Materialize a generated verifier dossier."""
    if root.exists():
        shutil.rmtree(root)
    (root / "tokens").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    write_json(root / "keys.json", {"keys": keys})
    write_json(root / "state.json", state)
    for index, token in enumerate(tokens):
        write_json(root / "tokens" / f"{index:02d}-{token['id']}.json", token)
    for name, data in (artifacts or {}).items():
        artifact_path = root / "artifacts" / name
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(data)
    return root / "keys.json", root / "tokens", root / "state.json"


def test_public_fixture_report_matches_jose_contract() -> None:
    """The bundled dossier must be audited by contract rules, not permissive checks."""
    build_gate()
    keys = APP / "fixtures" / "keys.json"
    tokens = APP / "fixtures" / "evidence"
    state = APP / "fixtures" / "state.json"
    before = {p: file_digest(p) for p in [keys, state, *sorted(tokens.glob("*.json"))]}
    out = WORK / "public" / "report.json"
    actual = run_gate(keys, tokens, state, out)
    assert actual == expected_report(keys, tokens, state)
    assert sorted(p.name for p in out.parent.iterdir()) == ["report.json"]
    assert {p: file_digest(p) for p in before} == before, "the gate rewrote evidence"
    assert set(actual) == {"accepted", "rejected", "summary"}


def test_generated_case_enforces_canonical_key_and_claim_boundaries() -> None:
    """Generated tokens exercise canonical encoding, keys, signatures, and windows."""
    build_gate()
    good_key = b"generated-signing-key-0000000000001"
    enc_key = b"generated-key-for-encryption-only---"
    artifacts = {
        "hosts/case-a.bin": b"case-a admitted artifact\nwith two slices\n",
        "hosts/expired.bin": b"expired artifact\n",
        "hosts/noncanonical.bin": b"noncanonical artifact\n",
        "hosts/enc-key.bin": b"encrypted key artifact\n",
        "hosts/bad-header.bin": b"bad header artifact\n",
        "hosts/rotated.bin": b"rotated key artifact\n",
        "hosts/wrong-digest.bin": b"artifact digest does not match claim\n",
        "hosts/bad-segment.bin": b"segment digest does not match claim\n",
    }
    keys = [
        {
            "kid": "sig-a",
            "kty": "oct",
            "alg": "HS256",
            "use": "sig",
            "k": b64(good_key),
            "nbf": NOW - 100,
            "exp": NOW + 100,
        },
        {
            "kid": "enc-a",
            "kty": "oct",
            "alg": "HS256",
            "use": "enc",
            "k": b64(enc_key),
            "nbf": NOW - 100,
            "exp": NOW + 100,
        },
    ]
    good = make_claim(
        evidence_id="good-1",
        subject="case-a",
        nonce="n1",
        seq=1,
        artifact="hosts/case-a.bin",
        artifact_bytes=artifacts["hosts/case-a.bin"],
        previous=None,
    )
    good_digest = good["sha256"]
    expired = make_claim(
        evidence_id="expired",
        subject="case-a",
        nonce="n2",
        seq=2,
        artifact="hosts/expired.bin",
        artifact_bytes=artifacts["hosts/expired.bin"],
        previous=good_digest,
        exp=NOW - 1,
    )
    noncanonical = make_claim(
        evidence_id="noncanonical",
        subject="case-a",
        nonce="n3",
        seq=2,
        artifact="hosts/noncanonical.bin",
        artifact_bytes=artifacts["hosts/noncanonical.bin"],
        previous=good_digest,
    )
    enc_claim = make_claim(
        evidence_id="enc-key",
        subject="case-b",
        nonce="n4",
        seq=1,
        artifact="hosts/enc-key.bin",
        artifact_bytes=artifacts["hosts/enc-key.bin"],
        previous=None,
    )
    bad_header_claim = make_claim(
        evidence_id="bad-header",
        subject="case-c",
        nonce="n6",
        seq=1,
        artifact="hosts/bad-header.bin",
        artifact_bytes=artifacts["hosts/bad-header.bin"],
        previous=None,
    )
    rotated = make_claim(
        evidence_id="rotated",
        subject="case-d",
        nonce="n7",
        seq=1,
        artifact="hosts/rotated.bin",
        artifact_bytes=artifacts["hosts/rotated.bin"],
        previous=None,
        event_time=NOW + 200,
        exp=NOW + 300,
    )
    bad_path = make_claim(
        evidence_id="bad-path",
        subject="case-e",
        nonce="n8",
        seq=1,
        artifact="../escape.bin",
        artifact_bytes=b"not materialized",
        previous=None,
    )
    bad_digest = make_claim(
        evidence_id="bad-digest",
        subject="case-f",
        nonce="n9",
        seq=1,
        artifact="hosts/wrong-digest.bin",
        artifact_bytes=artifacts["hosts/wrong-digest.bin"],
        previous=None,
        sha256="0" * 64,
    )
    bad_segment = make_claim(
        evidence_id="bad-segment",
        subject="case-g",
        nonce="n10",
        seq=1,
        artifact="hosts/bad-segment.bin",
        artifact_bytes=artifacts["hosts/bad-segment.bin"],
        previous=None,
    )
    bad_segment["segments"] = [
        {"offset": 0, "length": 5, "sha256": "1" * 64},
        {
            "offset": 5,
            "length": len(artifacts["hosts/bad-segment.bin"]) - 5,
            "sha256": digest(artifacts["hosts/bad-segment.bin"][5:]),
        },
    ]
    bad_sig_claim = make_claim(
        evidence_id="bad-sig",
        subject="case-c",
        nonce="n5",
        seq=1,
        artifact="hosts/bad-header.bin",
        artifact_bytes=artifacts["hosts/bad-header.bin"],
        previous=None,
    )
    tokens = [
        make_token("good-1", "sig-a", good_key, good),
        make_token("expired", "sig-a", good_key, expired),
        make_token(
            "noncanonical",
            "sig-a",
            good_key,
            noncanonical,
            payload_override=json.dumps(
                noncanonical,
                separators=(", ", ":"),
                sort_keys=True,
            ).encode(),
        ),
        make_token("enc-key", "enc-a", enc_key, enc_claim),
        make_token("bad-sig", "sig-a", good_key, bad_sig_claim, corrupt_signature=True),
        {"id": "malformed", "protected": "not enough fields"},
        make_token(
            "bad-header",
            "sig-a",
            good_key,
            bad_header_claim,
            header_override=canonical({"alg": "HS256", "kid": "sig-a", "typ": "evidence+jws"}),
        ),
        make_token("rotated", "sig-a", good_key, rotated),
        make_token("bad-path", "sig-a", good_key, bad_path),
        make_token("bad-digest", "sig-a", good_key, bad_digest),
        make_token("bad-segment", "sig-a", good_key, bad_segment),
    ]
    keys_path, tokens_dir, state_path = make_case(
        WORK / "boundaries",
        tokens,
        keys,
        {"accepted": []},
        artifacts,
    )
    nested_claim = make_claim(
        evidence_id="ignored",
        subject="nested",
        nonce="nested",
        seq=1,
        artifact="hosts/case-a.bin",
        artifact_bytes=artifacts["hosts/case-a.bin"],
        previous=None,
    )
    write_json(
        tokens_dir / "nested" / "00-ignored.json",
        make_token("ignored", "sig-a", good_key, nested_claim),
    )
    actual = run_gate(keys_path, tokens_dir, state_path, WORK / "boundaries" / "report.json")
    assert actual == expected_report(keys_path, tokens_dir, state_path)
    assert [row["id"] for row in actual["accepted"]] == ["good-1"]
    assert [row["reason"] for row in actual["rejected"]] == [
        "claim_window",
        "noncanonical",
        "unknown_kid",
        "bad_signature",
        "malformed",
        "bad_header",
        "key_window",
        "artifact_path",
        "artifact_digest",
        "artifact_digest",
    ]


def test_duplicate_jwk_kid_makes_referenced_key_unusable() -> None:
    """A duplicated JWK kid must be rejected while other keys remain usable."""
    build_gate()
    first_dup = b"duplicate-signing-key-primary--01"
    second_dup = b"duplicate-signing-key-shadow---02"
    control_key = b"unique-control-signing-key----03"
    artifacts = {
        "dup/token.bin": b"duplicate key artifact\n",
        "dup/control.bin": b"control key artifact\n",
    }
    keys = [
        {
            "kid": "dup-a",
            "kty": "oct",
            "alg": "HS256",
            "use": "sig",
            "k": b64(first_dup),
            "nbf": NOW - 100,
            "exp": NOW + 100,
        },
        {
            "kid": "dup-a",
            "kty": "oct",
            "alg": "HS256",
            "use": "sig",
            "k": b64(second_dup),
            "nbf": NOW - 100,
            "exp": NOW + 100,
        },
        {
            "kid": "solo-a",
            "kty": "oct",
            "alg": "HS256",
            "use": "sig",
            "k": b64(control_key),
            "nbf": NOW - 100,
            "exp": NOW + 100,
        },
    ]
    dup_claim = make_claim(
        evidence_id="dup-token",
        subject="duplicate-key-case",
        nonce="dup-nonce",
        seq=1,
        artifact="dup/token.bin",
        artifact_bytes=artifacts["dup/token.bin"],
        previous=None,
    )
    control_claim = make_claim(
        evidence_id="control-token",
        subject="control-subject",
        nonce="control-nonce",
        seq=1,
        artifact="dup/control.bin",
        artifact_bytes=artifacts["dup/control.bin"],
        previous=None,
    )
    tokens = [
        make_token("dup-token", "dup-a", first_dup, dup_claim),
        make_token("control-token", "solo-a", control_key, control_claim),
    ]
    keys_path, tokens_dir, state_path = make_case(
        WORK / "duplicate-kid",
        tokens,
        keys,
        {"accepted": []},
        artifacts,
    )
    actual = run_gate(keys_path, tokens_dir, state_path, WORK / "duplicate-kid" / "report.json")
    assert actual == expected_report(keys_path, tokens_dir, state_path)
    assert actual["rejected"] == [{"id": "dup-token", "reason": "unknown_kid"}]
    assert [row["id"] for row in actual["accepted"]] == ["control-token"]


def test_replay_order_uses_only_records_that_were_admitted() -> None:
    """Replay memory must be updated after full admission and preserve chains."""
    build_gate()
    key = b"replay-order-signing-key-000000000"
    keys = [
        {
            "kid": "sig-r",
            "kty": "oct",
            "alg": "HS256",
            "use": "sig",
            "k": b64(key),
            "nbf": NOW - 100,
            "exp": NOW + 100,
        }
    ]
    artifacts = {
        "replay/failed.bin": b"failed signature is not admitted\n",
        "replay/after.bin": b"accepted after a failed nonce use\n",
        "replay/next.bin": b"next accepted chain link\n",
        "replay/dup.bin": b"duplicate nonce after admission\n",
        "replay/old.bin": b"old sequence after admission\n",
        "replay/bad-chain.bin": b"bad chain for new subject\n",
    }
    prior = "1" * 64
    state = {
        "accepted": [
            {
                "kid": "sig-r",
                "nonce": "old",
                "subject": "asset-9",
                "seq": 10,
                "sha256": prior,
            }
        ]
    }
    failed_first = make_claim(
        evidence_id="failed-first",
        subject="asset-9",
        nonce="reuse-me",
        seq=11,
        artifact="replay/failed.bin",
        artifact_bytes=artifacts["replay/failed.bin"],
        previous=prior,
    )
    after_claim = make_claim(
        evidence_id="accepted-after-failure",
        subject="asset-9",
        nonce="reuse-me",
        seq=11,
        artifact="replay/after.bin",
        artifact_bytes=artifacts["replay/after.bin"],
        previous=prior,
    )
    next_claim = make_claim(
        evidence_id="next-good",
        subject="asset-9",
        nonce="fresh",
        seq=12,
        artifact="replay/next.bin",
        artifact_bytes=artifacts["replay/next.bin"],
        previous=after_claim["sha256"],
    )
    duplicate_nonce = make_claim(
        evidence_id="duplicate-nonce",
        subject="asset-9",
        nonce="fresh",
        seq=13,
        artifact="replay/dup.bin",
        artifact_bytes=artifacts["replay/dup.bin"],
        previous=next_claim["sha256"],
    )
    old_seq = make_claim(
        evidence_id="old-seq",
        subject="asset-9",
        nonce="old-seq",
        seq=12,
        artifact="replay/old.bin",
        artifact_bytes=artifacts["replay/old.bin"],
        previous=next_claim["sha256"],
    )
    bad_chain = make_claim(
        evidence_id="bad-chain",
        subject="new-subject",
        nonce="new-subject",
        seq=1,
        artifact="replay/bad-chain.bin",
        artifact_bytes=artifacts["replay/bad-chain.bin"],
        previous=digest(artifacts["replay/bad-chain.bin"]),
    )
    tokens = [
        make_token("failed-first", "sig-r", key, failed_first, corrupt_signature=True),
        make_token("accepted-after-failure", "sig-r", key, after_claim),
        make_token("next-good", "sig-r", key, next_claim),
        make_token("duplicate-nonce", "sig-r", key, duplicate_nonce),
        make_token("old-seq", "sig-r", key, old_seq),
        make_token("bad-chain", "sig-r", key, bad_chain),
    ]
    keys_path, tokens_dir, state_path = make_case(
        WORK / "replay",
        tokens,
        keys,
        state,
        artifacts,
    )
    actual = run_gate(keys_path, tokens_dir, state_path, WORK / "replay" / "report.json")
    assert actual == expected_report(keys_path, tokens_dir, state_path)
    assert [row["id"] for row in actual["accepted"]] == [
        "accepted-after-failure",
        "next-good",
    ]
    assert [row["reason"] for row in actual["rejected"]] == [
        "bad_signature",
        "replay",
        "sequence",
        "chain",
    ]


def test_segment_ledger_binds_every_artifact_byte() -> None:
    """Segment offsets, lengths, and digests must bind the full artifact bytes."""
    build_gate()
    key = b"segment-ledger-signing-key-000000"
    keys = [
        {
            "kid": "sig-s",
            "kty": "oct",
            "alg": "HS256",
            "use": "sig",
            "k": b64(key),
            "nbf": NOW - 100,
            "exp": NOW + 100,
        }
    ]
    artifacts = {
        "seg/good.bin": b"alpha beta gamma delta epsilon\n",
        "seg/gap.bin": b"gap in segment coverage\n",
        "seg/size.bin": b"wrong artifact size\n",
        "seg/zero.bin": b"zero length segment\n",
    }
    good_claim = make_claim(
        evidence_id="seg-good",
        subject="seg",
        nonce="s1",
        seq=1,
        artifact="seg/good.bin",
        artifact_bytes=artifacts["seg/good.bin"],
        previous=None,
    )
    gap_segments = segments_for(artifacts["seg/gap.bin"])
    gap_segments[1]["offset"] = gap_segments[1]["offset"] + 1
    gap_claim = make_claim(
        evidence_id="seg-gap",
        subject="seg-gap",
        nonce="s2",
        seq=1,
        artifact="seg/gap.bin",
        artifact_bytes=artifacts["seg/gap.bin"],
        previous=None,
        segments=gap_segments,
    )
    size_claim = make_claim(
        evidence_id="seg-size",
        subject="seg-size",
        nonce="s3",
        seq=1,
        artifact="seg/size.bin",
        artifact_bytes=artifacts["seg/size.bin"],
        previous=None,
        artifact_size=len(artifacts["seg/size.bin"]) + 1,
    )
    zero_segments = [
        {"offset": 0, "length": 0, "sha256": digest(b"")},
        {
            "offset": 0,
            "length": len(artifacts["seg/zero.bin"]),
            "sha256": digest(artifacts["seg/zero.bin"]),
        },
    ]
    zero_claim = make_claim(
        evidence_id="seg-zero",
        subject="seg-zero",
        nonce="s4",
        seq=1,
        artifact="seg/zero.bin",
        artifact_bytes=artifacts["seg/zero.bin"],
        previous=None,
        segments=zero_segments,
    )
    tokens = [
        make_token("seg-good", "sig-s", key, good_claim),
        make_token("seg-gap", "sig-s", key, gap_claim),
        make_token("seg-size", "sig-s", key, size_claim),
        make_token("seg-zero", "sig-s", key, zero_claim),
    ]
    keys_path, tokens_dir, state_path = make_case(
        WORK / "segments",
        tokens,
        keys,
        {"accepted": []},
        artifacts,
    )
    actual = run_gate(keys_path, tokens_dir, state_path, WORK / "segments" / "report.json")
    assert actual == expected_report(keys_path, tokens_dir, state_path)
    assert [row["id"] for row in actual["accepted"]] == ["seg-good"]
    assert [row["reason"] for row in actual["rejected"]] == [
        "artifact_digest",
        "artifact_digest",
        "artifact_digest",
    ]


def test_report_is_reproducible_and_contains_only_documented_schema() -> None:
    """Repeated audits over the same dossier must produce identical bytes and fields."""
    build_gate()
    out1 = WORK / "determinism" / "one.json"
    out2 = WORK / "determinism" / "two.json"
    keys = APP / "fixtures" / "keys.json"
    tokens = APP / "fixtures" / "evidence"
    state = APP / "fixtures" / "state.json"
    report = run_gate(keys, tokens, state, out1)
    assert run_gate(keys, tokens, state, out2) == report
    assert out1.read_bytes() == out2.read_bytes()
    assert all(set(row) == {"id", "subject", "kid", "seq", "sha256"} for row in report["accepted"])
    assert all(set(row) == {"id", "reason"} for row in report["rejected"])
    assert set(report["summary"]) == {"accepted", "rejected"}
