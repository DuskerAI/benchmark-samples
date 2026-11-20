import base64
import grp
import hashlib
import hmac
import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path("/app/sigcheck")
BUILD = ROOT / "bin" / "build.sh"
CLI = ROOT / "bin" / "sigcheck"
TESTS_DIR = Path("/tests")
CANDIDATE_HOME = Path("/tmp/sigcheck-candidate-home")
KEY = b"local-attestation-secret-32bytes!!"
KEY_ID = "root-ci-1"
ISSUER = "https://issuer.example/oidc"
SUBJECT = "https://github.com/example/project/.github/workflows/release.yml@refs/tags/v1.2.3"
CERT_SUBJECT = "CN=example release certificate"
REQUIRED_REPORT_FIELDS = {"ok", "errors", "subject", "issuer", "logIndex"}


def candidate_identity():
    user = pwd.getpwnam("nobody")
    try:
        gid = grp.getgrnam("nogroup").gr_gid
    except KeyError:
        gid = user.pw_gid
    return user.pw_uid, gid


CANDIDATE_UID, CANDIDATE_GID = candidate_identity()


def candidate_env():
    env = os.environ.copy()
    env["HOME"] = str(CANDIDATE_HOME)
    return env


def chown_tree(path: Path):
    for current, dirs, files in os.walk(path):
        os.chown(current, CANDIDATE_UID, CANDIDATE_GID)
        os.chmod(current, 0o755)
        for name in dirs:
            os.chown(Path(current) / name, CANDIDATE_UID, CANDIDATE_GID)
            os.chmod(Path(current) / name, 0o755)
        for name in files:
            file_path = Path(current) / name
            executable = file_path.stat().st_mode & 0o111
            os.chown(file_path, CANDIDATE_UID, CANDIDATE_GID)
            os.chmod(file_path, 0o755 if executable else 0o644)


def open_tmp_ancestors(path: Path):
    for parent in [path, *path.parents]:
        if parent == Path("/tmp"):
            break
        if str(parent).startswith("/tmp/"):
            os.chmod(parent, 0o755)


def run_as_candidate(args, *, cwd: Path, **kwargs):
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=candidate_env(),
        user=CANDIDATE_UID,
        group=CANDIDATE_GID,
        **kwargs,
    )


def protect_tests_from_candidate():
    os.chmod(TESTS_DIR, 0o700)
    probe = run_as_candidate(
        ["/bin/sh", "-c", f"test ! -r {TESTS_DIR / 'test_outputs.py'}"],
        cwd=Path("/tmp"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert probe.returncode == 0, "candidate build/CLI user can still read /tests"


def prepare_candidate_tree():
    CANDIDATE_HOME.mkdir(parents=True, exist_ok=True)
    shutil.chown(CANDIDATE_HOME, user=CANDIDATE_UID, group=CANDIDATE_GID)
    chown_tree(ROOT)


def assert_complete_report(data):
    assert set(data) == REQUIRED_REPORT_FIELDS
    assert isinstance(data["ok"], bool)
    assert isinstance(data["errors"], list)
    assert all(isinstance(item, str) for item in data["errors"])
    assert isinstance(data["subject"], str)
    assert isinstance(data["issuer"], str)
    assert isinstance(data["logIndex"], int) and not isinstance(data["logIndex"], bool)


@pytest.fixture(scope="session", autouse=True)
def _build_cli():
    protect_tests_from_candidate()
    prepare_candidate_tree()
    run_as_candidate([str(BUILD)], cwd=ROOT, check=True)


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def pae(payload_type: str, payload: bytes) -> bytes:
    head = f"DSSEv1 {len(payload_type.encode())} {payload_type} {len(payload)} ".encode()
    return head + payload


def parent(left: bytes, right: bytes) -> bytes:
    return digest(b"\x01" + left + right)


def leaf(body) -> bytes:
    return digest(b"\x00" + canonical(body))


def proof_for(leaves, index):
    level = leaves[:]
    idx = index
    proof = []
    while len(level) > 1:
        proof.append(level[idx ^ 1])
        level = [parent(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        idx //= 2
    return proof, level[0]


def refresh_entry(bundle_obj, *, leaf_index=None, tree_size=None):
    entry = bundle_obj["verificationMaterial"]["tlogEntries"][0]
    if leaf_index is None:
        leaf_index = entry.get("logIndex", 3)
    if tree_size is None:
        tree_size = entry.get("checkpoint", {}).get("treeSize", 4)
    body = bundle_obj["verificationMaterial"]["tlogEntries"][0]["body"]
    leaves = [digest(f"side:{i}".encode()) for i in range(tree_size)]
    leaves[leaf_index] = leaf(body)
    proof, root = proof_for(leaves, leaf_index)
    entry["checkpoint"]["rootHash"] = b64u(root)
    entry["inclusionProof"] = [b64u(x) for x in proof]
    entry["logIndex"] = leaf_index
    entry["checkpoint"]["treeSize"] = len(leaves)


def resign_bundle(bundle_path: Path, bundle_obj, *, key: bytes = KEY):
    envelope = bundle_obj["dsseEnvelope"]
    payload_type = envelope["payloadType"]
    statement = json.loads(base64.urlsafe_b64decode(envelope["payload"] + "=="))
    payload = canonical(statement)
    envelope["payload"] = b64u(payload)
    envelope["signatures"][0]["sig"] = b64u(hmac.new(key, pae(payload_type, payload), hashlib.sha256).digest())
    bundle_obj["verificationMaterial"]["tlogEntries"][0]["body"]["payloadHash"] = hashlib.sha256(payload).hexdigest()
    refresh_entry(bundle_obj)
    bundle_path.write_text(json.dumps(bundle_obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")


def write_case(
    tmp_path: Path,
    *,
    artifact=b"release artifact\n",
    payload_type="application/vnd.in-toto+json",
    san=None,
    signing_time=1_700_000_000,
    not_before=1_700_000_000,
    not_after=1_700_000_100,
    leaf_index=3,
    cert_subject=CERT_SUBJECT,
    body_subject=SUBJECT,
    tag="rélease-π",
    tree_size=4,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    artifact_path = tmp_path / "artifact.bin"
    artifact_path.write_bytes(artifact)
    trust = {
        "trustedKeys": [{"keyid": KEY_ID, "key": b64u(KEY)}],
        "issuers": [ISSUER],
        "subjects": [SUBJECT],
        "logs": ["local-log"],
    }
    statement = {
        "predicate": {"builder": {"id": "github-actions"}, "source": {"uri": "https://example.invalid/rélease/π"}},
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [{"digest": {"sha256": hashlib.sha256(artifact).hexdigest()}, "name": "artifact"}],
        "_type": "https://in-toto.io/Statement/v1",
    }
    payload = canonical(statement)
    signature = hmac.new(KEY, pae(payload_type, payload), hashlib.sha256).digest()
    envelope = {
        "payload": b64u(payload),
        "payloadType": payload_type,
        "signatures": [{"keyid": KEY_ID, "sig": b64u(signature)}],
    }
    if san is None:
        san = [{"type": "uri", "value": SUBJECT}]
    cert = {
        "issuer": ISSUER,
        "keyid": KEY_ID,
        "notAfter": not_after,
        "notBefore": not_before,
        "san": san,
        "subject": cert_subject,
    }
    body = {
        "subject": body_subject,
        "issuer": ISSUER,
        "integratedTime": signing_time,
        "payloadHash": hashlib.sha256(payload).hexdigest(),
        "tag": tag,
    }
    leaves = [digest(f"side:{i}".encode()) for i in range(tree_size)]
    leaves[leaf_index] = leaf(body)
    proof, root = proof_for(leaves, leaf_index)
    entry = {
        "body": body,
        "checkpoint": {"logId": "local-log", "rootHash": b64u(root), "treeSize": tree_size},
        "inclusionProof": [b64u(x) for x in proof],
        "integratedTime": signing_time,
        "logIndex": leaf_index,
    }
    bundle = {
        "mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.3",
        "dsseEnvelope": envelope,
        "verificationMaterial": {"certificate": cert, "tlogEntries": [entry]},
    }
    trust_path = tmp_path / "trust.json"
    bundle_path = tmp_path / "bundle.json"
    report_path = tmp_path / "report.json"
    trust_path.write_text(json.dumps(trust, separators=(",", ":")), encoding="utf-8")
    bundle_path.write_text(json.dumps(bundle, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    return trust_path, bundle_path, artifact_path, report_path, bundle


def run_cli(trust, bundle, artifact, report):
    open_tmp_ancestors(report.parent)
    chown_tree(report.parent)
    cp = run_as_candidate(
        [str(CLI), "--trust", str(trust), "--bundle", str(bundle), "--artifact", str(artifact), "--out", str(report)],
        cwd=report.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    data = json.loads(report.read_text()) if report.exists() else {}
    assert_complete_report(data)
    return cp, data


def test_accepts_complete_local_evidence(tmp_path):
    """A complete offline attestation with matching policy evidence is accepted."""
    trust, bundle, artifact, report, _ = write_case(tmp_path)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode == 0, cp.stderr
    assert data["ok"] is True
    assert data["errors"] == []
    assert data["subject"] == SUBJECT
    assert data["subject"] != CERT_SUBJECT
    assert data["issuer"] == ISSUER
    assert data["logIndex"] == 3


def test_accepts_complete_local_evidence_at_alternate_log_index(tmp_path):
    """A complete attestation remains valid when the transparency proof uses another leaf index."""
    trust, bundle, artifact, report, _ = write_case(tmp_path, leaf_index=6, tree_size=8)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode == 0, cp.stderr
    assert data["ok"] is True
    assert data["errors"] == []
    assert data["logIndex"] == 6


def test_rejects_inconsistent_attestation_material(tmp_path):
    """Evidence that no longer binds the local artifact is rejected."""
    trust, bundle, artifact, report, _ = write_case(tmp_path, artifact=b"first artifact\n")
    artifact.write_bytes(b"second artifact\n")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False
    assert data["errors"]


def test_rejected_report_preserves_complete_schema(tmp_path):
    """Rejected evidence still produces the complete public report schema."""
    trust, bundle, artifact, report, _ = write_case(tmp_path, artifact=b"first artifact\n")
    artifact.write_bytes(b"second artifact\n")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert_complete_report(data)
    assert data["ok"] is False


def test_malformed_report_preserves_complete_schema(tmp_path):
    """Malformed local evidence still produces the complete public report schema."""
    trust, bundle, artifact, report, _ = write_case(tmp_path)
    bundle.write_text('{"mediaType":', encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert_complete_report(data)
    assert data["ok"] is False


def test_rejects_forged_signature_over_valid_payload(tmp_path):
    """A bundle with otherwise matching evidence must still be rejected when its DSSE signature is forged."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["dsseEnvelope"]["signatures"][0]["sig"] = b64u(digest(b"forged-signature"))
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_unknown_signature_keyid(tmp_path):
    """The DSSE signature key identifier must select a trusted local key."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["dsseEnvelope"]["signatures"][0]["keyid"] = "missing-ci-key"
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_duplicate_trusted_key_identifier(tmp_path):
    """Trust policy key identifiers are exact selectors and cannot be ambiguous."""
    trust, bundle, artifact, report, _ = write_case(tmp_path)
    trust_obj = json.loads(trust.read_text(encoding="utf-8"))
    trust_obj["trustedKeys"].append({"keyid": KEY_ID, "key": b64u(b"shadow-local-key-32-byte-secret!")})
    trust.write_text(json.dumps(trust_obj, separators=(",", ":")), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_ambiguous_policy_allowlists(tmp_path):
    """Issuer, subject, and log allowlists must be non-empty exact-match sets."""
    for field, duplicate_value in [("issuers", ISSUER), ("subjects", SUBJECT), ("logs", "local-log")]:
        trust, bundle, artifact, report, _ = write_case(tmp_path / field)
        trust_obj = json.loads(trust.read_text(encoding="utf-8"))
        trust_obj[field].append(duplicate_value)
        trust.write_text(json.dumps(trust_obj, separators=(",", ":")), encoding="utf-8")
        cp, data = run_cli(trust, bundle, artifact, report)
        assert cp.returncode != 0
        assert data["ok"] is False

    for field in ["issuers", "subjects", "logs"]:
        trust, bundle, artifact, report, _ = write_case(tmp_path / f"empty-{field}")
        trust_obj = json.loads(trust.read_text(encoding="utf-8"))
        trust_obj[field] = [""]
        trust.write_text(json.dumps(trust_obj, separators=(",", ":")), encoding="utf-8")
        cp, data = run_cli(trust, bundle, artifact, report)
        assert cp.returncode != 0
        assert data["ok"] is False

    trust, bundle, artifact, report, _ = write_case(tmp_path / "empty-log-array")
    trust_obj = json.loads(trust.read_text(encoding="utf-8"))
    trust_obj["logs"] = []
    trust.write_text(json.dumps(trust_obj, separators=(",", ":")), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False

    trust, bundle, artifact, report, _ = write_case(tmp_path / "typed-log")
    trust_obj = json.loads(trust.read_text(encoding="utf-8"))
    trust_obj["logs"] = [7]
    trust.write_text(json.dumps(trust_obj, separators=(",", ":")), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_trusted_key_material_with_wrong_length(tmp_path):
    """A trusted signing key must have enough decoded material for local admission."""
    short_key = b"short-local-key"
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    trust_obj = json.loads(trust.read_text(encoding="utf-8"))
    trust_obj["trustedKeys"][0]["key"] = b64u(short_key)
    trust.write_text(json.dumps(trust_obj, separators=(",", ":")), encoding="utf-8")
    resign_bundle(bundle, obj, key=short_key)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_wrong_envelope_domain(tmp_path):
    """Signed material using the wrong envelope domain is not accepted."""
    trust, bundle, artifact, report, _ = write_case(tmp_path, payload_type="application/example")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_wrong_bundle_media_type(tmp_path):
    """The bundle media type must match the offline policy exactly."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["mediaType"] = "application/vnd.dev.sigstore.bundle+json;version=0.2"
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_untrusted_identity_shape(tmp_path):
    """Certificate identity evidence must match the local trust policy unambiguously."""
    san = [
        {"type": "uri", "value": SUBJECT},
        {"type": "uri", "value": "https://github.com/other/project/.github/workflows/release.yml@refs/tags/v1.2.3"},
    ]
    trust, bundle, artifact, report, _ = write_case(tmp_path, san=san)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_certificate_with_extra_non_uri_san(tmp_path):
    """Certificate SAN evidence must contain one URI identity and no unrelated identities."""
    san = [
        {"type": "uri", "value": SUBJECT},
        {"type": "dns", "value": "example.com"},
    ]
    trust, bundle, artifact, report, _ = write_case(tmp_path, san=san)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_single_well_formed_but_untrusted_san_uri(tmp_path):
    """A single URI SAN that is syntactically valid but outside the trust policy is rejected."""
    untrusted_subject = "https://github.com/other/project/.github/workflows/release.yml@refs/tags/v9.9.9"
    san = [{"type": "uri", "value": untrusted_subject}]
    trust, bundle, artifact, report, _ = write_case(tmp_path, san=san, body_subject=untrusted_subject)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_untrusted_certificate_issuer(tmp_path):
    """Certificate issuers outside the local trust policy are rejected."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    rogue_issuer = "https://issuer.example/rogue"
    obj["verificationMaterial"]["certificate"]["issuer"] = rogue_issuer
    obj["verificationMaterial"]["tlogEntries"][0]["body"]["issuer"] = rogue_issuer
    refresh_entry(obj)
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_certificate_keyid_that_does_not_match_signature_keyid(tmp_path):
    """The certificate key identifier must match the key identifier used for the DSSE signature."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["verificationMaterial"]["certificate"]["keyid"] = "other-ci-key"
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_padded_base64url_signature_material(tmp_path):
    """Base64url evidence is invalid when it uses padding instead of the unpadded policy form."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["dsseEnvelope"]["signatures"][0]["sig"] += "="
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_accepts_certificate_window_boundary(tmp_path):
    """The local policy treats the stated signing instant as part of the certificate window."""
    trust, bundle, artifact, report, _ = write_case(tmp_path, signing_time=1_700_000_100, not_before=1_700_000_000, not_after=1_700_000_100)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode == 0, data
    assert data["ok"] is True


@pytest.mark.parametrize(
    "signing_time,not_before,not_after",
    [
        pytest.param(1_699_999_999, 1_700_000_000, 1_700_000_100, id="before-window"),
        pytest.param(1_700_000_101, 1_700_000_000, 1_700_000_100, id="after-window"),
    ],
)
def test_rejects_certificate_window_outside_bounds(tmp_path, signing_time, not_before, not_after):
    """Certificate evidence is rejected when the signing instant is outside the validity window."""
    trust, bundle, artifact, report, _ = write_case(
        tmp_path,
        signing_time=signing_time,
        not_before=not_before,
        not_after=not_after,
    )
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_negative_temporal_evidence(tmp_path):
    """Admission times use non-negative policy integers even when their ordering is otherwise consistent."""
    trust, bundle, artifact, report, _ = write_case(tmp_path, signing_time=0, not_before=-5, not_after=5)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_bad_checkpoint_evidence(tmp_path):
    """A plausible transparency entry with inconsistent checkpoint evidence is rejected."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["verificationMaterial"]["tlogEntries"][0]["inclusionProof"][0] = b64u(digest(b"replacement"))
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_untrusted_checkpoint_log(tmp_path):
    """The transparency checkpoint log identity must be in the local trust policy."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["verificationMaterial"]["tlogEntries"][0]["checkpoint"]["logId"] = "rogue-log"
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_checkpoint_tree_size_that_does_not_match_proof(tmp_path):
    """Transparency evidence must bind the stated tree size to the provided inclusion path."""
    trust, bundle, artifact, report, obj = write_case(tmp_path, leaf_index=6, tree_size=8)
    obj["verificationMaterial"]["tlogEntries"][0]["checkpoint"]["treeSize"] = 16
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_log_index_outside_tree_size_with_valid_low_bits(tmp_path):
    """The transparency log index must be rejected when it is outside the checkpoint tree size."""
    trust, bundle, artifact, report, obj = write_case(tmp_path, leaf_index=3, tree_size=4)
    obj["verificationMaterial"]["tlogEntries"][0]["logIndex"] = 7
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_checkpoint_tree_size_that_is_not_power_of_two(tmp_path):
    """The checkpoint tree size is invalid unless it is a positive power of two."""
    trust, bundle, artifact, report, obj = write_case(tmp_path, leaf_index=3, tree_size=8)
    obj["verificationMaterial"]["tlogEntries"][0]["checkpoint"]["treeSize"] = 6
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_empty_transparency_tag(tmp_path):
    """The transparency body tag is arbitrary but must be present and non-empty."""
    trust, bundle, artifact, report, _ = write_case(tmp_path, tag="")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_extra_transparency_body_fields(tmp_path):
    """The transparency leaf body schema must not accept unrelated fields."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["verificationMaterial"]["tlogEntries"][0]["body"]["unused"] = "shadow-value"
    refresh_entry(obj)
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_checkpoint_with_extra_policy_field(tmp_path):
    """The transparency checkpoint schema must be closed over the policy fields."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["verificationMaterial"]["tlogEntries"][0]["checkpoint"]["note"] = "shadow checkpoint"
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_tlog_body_bound_to_certificate_subject(tmp_path):
    """The transparency body subject must bind the certificate SAN URI identity."""
    trust, bundle, artifact, report, obj = write_case(tmp_path, body_subject=CERT_SUBJECT)
    refresh_entry(obj)
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_tlog_body_that_does_not_bind_payload(tmp_path):
    """The transparency leaf body must bind the accepted payload evidence."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["verificationMaterial"]["tlogEntries"][0]["body"]["payloadHash"] = hashlib.sha256(b"other-payload").hexdigest()
    refresh_entry(obj, leaf_index=2)
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_tlog_body_time_that_does_not_bind_entry_time(tmp_path):
    """The transparency body time must bind the entry signing time used for certificate validity."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    obj["verificationMaterial"]["tlogEntries"][0]["body"]["integratedTime"] = 1_700_000_001
    refresh_entry(obj)
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_non_canonical_json_material(tmp_path):
    """Malformed local evidence is rejected without a successful audit report."""
    trust, bundle, artifact, report, _ = write_case(tmp_path)
    text = bundle.read_text(encoding="utf-8")
    text = text.replace('"payloadType":"application/vnd.in-toto+json"', '"payloadType":"application/vnd.in-toto+json","payloadType":"application/example"', 1)
    bundle.write_text(text, encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_integer_with_leading_zero_in_json_material(tmp_path):
    """Integer numbers in local JSON evidence must use the canonical no-leading-zero form."""
    trust, bundle, artifact, report, _ = write_case(tmp_path)
    text = bundle.read_text(encoding="utf-8")
    text = text.replace('"integratedTime":1700000000', '"integratedTime":01700000000', 1)
    bundle.write_text(text, encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_unsorted_payload_serialization(tmp_path):
    """Signed statement bytes must use the canonical sorted-key representation."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    statement = json.loads(base64.urlsafe_b64decode(obj["dsseEnvelope"]["payload"] + "=="))
    unsorted_statement = {
        "subject": statement["subject"],
        "_type": statement["_type"],
        "predicateType": statement["predicateType"],
        "predicate": statement["predicate"],
    }
    unsorted_payload = json.dumps(unsorted_statement, sort_keys=False, separators=(",", ":"), ensure_ascii=False).encode()
    obj["dsseEnvelope"]["payload"] = b64u(unsorted_payload)
    obj["dsseEnvelope"]["signatures"][0]["sig"] = b64u(hmac.new(KEY, pae(obj["dsseEnvelope"]["payloadType"], unsorted_payload), hashlib.sha256).digest())
    obj["verificationMaterial"]["tlogEntries"][0]["body"]["payloadHash"] = hashlib.sha256(unsorted_payload).hexdigest()
    refresh_entry(obj)
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_statement_with_extra_top_level_field(tmp_path):
    """The signed in-toto statement schema is closed over the policy fields."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    statement = json.loads(base64.urlsafe_b64decode(obj["dsseEnvelope"]["payload"] + "=="))
    statement["buildConfig"] = {"mode": "release"}
    obj["dsseEnvelope"]["payload"] = b64u(canonical(statement))
    resign_bundle(bundle, obj)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_unexpected_statement_predicate_contract(tmp_path):
    """The signed provenance predicate must match the SLSA source and builder contract."""
    trust, bundle, artifact, report, obj = write_case(tmp_path / "predicate-type")
    statement = json.loads(base64.urlsafe_b64decode(obj["dsseEnvelope"]["payload"] + "=="))
    statement["predicateType"] = "https://slsa.dev/provenance/v0.2"
    obj["dsseEnvelope"]["payload"] = b64u(canonical(statement))
    resign_bundle(bundle, obj)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False

    trust, bundle, artifact, report, obj = write_case(tmp_path / "predicate-extra")
    statement = json.loads(base64.urlsafe_b64decode(obj["dsseEnvelope"]["payload"] + "=="))
    statement["predicate"]["buildType"] = "release"
    obj["dsseEnvelope"]["payload"] = b64u(canonical(statement))
    resign_bundle(bundle, obj)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False

    trust, bundle, artifact, report, obj = write_case(tmp_path / "builder-id")
    statement = json.loads(base64.urlsafe_b64decode(obj["dsseEnvelope"]["payload"] + "=="))
    statement["predicate"]["builder"]["id"] = ""
    obj["dsseEnvelope"]["payload"] = b64u(canonical(statement))
    resign_bundle(bundle, obj)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False

    trust, bundle, artifact, report, obj = write_case(tmp_path / "source-uri")
    statement = json.loads(base64.urlsafe_b64decode(obj["dsseEnvelope"]["payload"] + "=="))
    statement["predicate"]["source"]["uri"] = "git@example.invalid/release.git"
    obj["dsseEnvelope"]["payload"] = b64u(canonical(statement))
    resign_bundle(bundle, obj)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False

    trust, bundle, artifact, report, obj = write_case(tmp_path / "source-shape")
    statement = json.loads(base64.urlsafe_b64decode(obj["dsseEnvelope"]["payload"] + "=="))
    statement["predicate"]["source"] = "https://example.invalid/release"
    obj["dsseEnvelope"]["payload"] = b64u(canonical(statement))
    resign_bundle(bundle, obj)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_statement_digest_with_extra_algorithm(tmp_path):
    """The single statement subject digest must contain only the lowercase sha256 value."""
    trust, bundle, artifact, report, obj = write_case(tmp_path)
    statement = json.loads(base64.urlsafe_b64decode(obj["dsseEnvelope"]["payload"] + "=="))
    statement["subject"][0]["digest"]["sha512"] = hashlib.sha512(artifact.read_bytes()).hexdigest()
    obj["dsseEnvelope"]["payload"] = b64u(canonical(statement))
    resign_bundle(bundle, obj)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False


def test_rejects_ambiguous_bundle_cardinality(tmp_path):
    """Accepted bundles must contain one signature, one subject, and one transparency entry."""
    trust, bundle, artifact, report, obj = write_case(tmp_path / "signatures")
    obj["dsseEnvelope"]["signatures"].append(dict(obj["dsseEnvelope"]["signatures"][0]))
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False

    trust, bundle, artifact, report, obj = write_case(tmp_path / "subjects")
    statement = json.loads(base64.urlsafe_b64decode(obj["dsseEnvelope"]["payload"] + "=="))
    statement["subject"].append({"digest": {"sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}, "name": "artifact-copy"})
    obj["dsseEnvelope"]["payload"] = b64u(canonical(statement))
    resign_bundle(bundle, obj)
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False

    trust, bundle, artifact, report, obj = write_case(tmp_path / "tlog")
    obj["verificationMaterial"]["tlogEntries"].append(json.loads(json.dumps(obj["verificationMaterial"]["tlogEntries"][0])))
    bundle.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    cp, data = run_cli(trust, bundle, artifact, report)
    assert cp.returncode != 0
    assert data["ok"] is False
