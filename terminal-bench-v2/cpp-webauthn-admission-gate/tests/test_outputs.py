import base64
import hashlib
import hmac
import json
import subprocess
from pathlib import Path

import pytest

APP = Path("/app/webauthn_gate")
BIN = APP / "bin" / "webauthn-gate"
RP_ID = "vault.example"
ORIGIN = "https://vault.example"
AAGUID = "00112233445566778899aabbccddeeff"
ATTEST_KEY = "41" * 32
ASSERT_KEY = "99" * 32
ZERO_HASH = "00" * 32


@pytest.fixture(scope="session", autouse=True)
def build_gate():
    """The project builds the requested gate binary before behavior is checked."""
    result = subprocess.run(
        ["make", "-C", str(APP), "clean", "all"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert BIN.exists()


def write_json(path, obj):
    path.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")


def read_report(path):
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    report = json.loads(raw)
    assert set(report) == {
        "admitted",
        "status",
        "credential_id",
        "sign_count",
        "audit_hash",
        "violations",
    }
    assert isinstance(report["admitted"], bool)
    assert report["status"] in {"registered", "authenticated", "rejected", "malformed"}
    assert report["credential_id"] is None or isinstance(report["credential_id"], str)
    assert isinstance(report["sign_count"], int)
    assert report["audit_hash"] is None or isinstance(report["audit_hash"], str)
    assert isinstance(report["violations"], list)
    return report


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def client_data(kind, challenge="challenge-a", origin=ORIGIN):
    payload = {"type": kind, "challenge": challenge, "origin": origin}
    return json.dumps(payload, separators=(",", ":")).encode()


def cbor_head(major, length):
    if length < 24:
        return bytes([(major << 5) | length])
    if length < 256:
        return bytes([(major << 5) | 24, length])
    return bytes([(major << 5) | 25, length >> 8, length & 255])


def cbor_text(text):
    data = text.encode()
    return cbor_head(3, len(data)) + data


def cbor_bytes(data):
    return cbor_head(2, len(data)) + data


def cbor_map(items):
    out = bytearray(cbor_head(5, len(items)))
    for key, value in items:
        out.extend(cbor_text(key))
        if isinstance(value, str):
            out.extend(cbor_text(value))
        else:
            out.extend(cbor_bytes(value))
    return bytes(out)


def flip_last_hex_digit(hex_text):
    last = "0" if hex_text[-1].lower() != "0" else "1"
    return hex_text[:-1] + last


def sha_hex(data):
    return hashlib.sha256(data).hexdigest()


def audit_entry(seq, event_hash, prev_hash, credential_id, sign_count, status):
    body = (
        bytes.fromhex(event_hash)
        + bytes.fromhex(prev_hash)
        + bytes.fromhex(credential_id)
        + sign_count.to_bytes(8, "big")
        + status.encode()
    )
    return {
        "seq": seq,
        "event_hash": event_hash,
        "status": status,
        "credential_id": credential_id,
        "sign_count": sign_count,
        "prev_hash": prev_hash,
        "entry_hash": sha_hex(body),
    }


def current_audit_hash(state_text):
    audit = json.loads(state_text).get("audit", [])
    return audit[-1]["entry_hash"] if audit else ZERO_HASH


def policy_obj(challenges=None):
    return {
        "rp_id": RP_ID,
        "origins": [ORIGIN],
        "challenges": challenges or ["challenge-a", "challenge-b"],
        "trusted_aaguids": [
            {
                "aaguid": AAGUID,
                "attestation_key": ATTEST_KEY,
                "assertion_key": ASSERT_KEY,
            }
        ],
    }


def auth_data(
    counter,
    *,
    rp_id=RP_ID,
    flags=0x05,
    credential_id=b"",
    include_attested=False,
    credential_public_key=b"\xA0",
):
    data = bytearray(hashlib.sha256(rp_id.encode()).digest())
    data.append(flags | (0x40 if include_attested else 0))
    data.extend(counter.to_bytes(4, "big"))
    if include_attested:
        data.extend(bytes.fromhex(AAGUID))
        data.extend(len(credential_id).to_bytes(2, "big"))
        data.extend(credential_id)
        data.extend(credential_public_key)
    return bytes(data)


def registration_event(
    credential_id=b"cred-1",
    counter=1,
    *,
    rp_id=RP_ID,
    flags=0x05,
    client_kind="webauthn.create",
    challenge="challenge-a",
    origin=ORIGIN,
    aaguid=AAGUID,
    prev_hash=ZERO_HASH,
):
    client = client_data(client_kind, challenge=challenge, origin=origin)
    ad = auth_data(
        counter,
        rp_id=rp_id,
        flags=flags,
        credential_id=credential_id,
        include_attested=True,
    )
    nonce = b"registration nonce"
    sig = hmac.new(
        bytes.fromhex(ATTEST_KEY),
        ad + hashlib.sha256(client).digest() + nonce + bytes.fromhex(prev_hash),
        hashlib.sha256,
    ).digest()
    att = cbor_map(
        [
            ("fmt", "packed"),
            ("aaguid", bytes.fromhex(aaguid)),
            ("credential_id", credential_id),
            ("nonce", nonce),
            ("sig", sig),
        ]
    )
    return {
        "kind": "registration",
        "credential_id": credential_id.hex(),
        "client_data_json": b64url(client),
        "authenticator_data": ad.hex(),
        "attestation_object": att.hex(),
    }


def authentication_event(
    credential_id=b"cred-1",
    counter=6,
    *,
    challenge="challenge-a",
    rp_id=RP_ID,
    flags=0x05,
    prev_hash=ZERO_HASH,
):
    client = client_data("webauthn.get", challenge=challenge)
    ad = auth_data(counter, rp_id=rp_id, flags=flags)
    sig = hmac.new(
        bytes.fromhex(ASSERT_KEY),
        credential_id + ad + hashlib.sha256(client).digest() + bytes.fromhex(prev_hash),
        hashlib.sha256,
    ).digest()
    return {
        "kind": "authentication",
        "credential_id": credential_id.hex(),
        "client_data_json": b64url(client),
        "authenticator_data": ad.hex(),
        "assertion_signature": sig.hex(),
    }


def run_gate(tmp_path, policy, event, state_text):
    policy_path = tmp_path / "policy.json"
    event_path = tmp_path / "event.json"
    state_path = tmp_path / "state.json"
    report_path = tmp_path / "report.json"
    write_json(policy_path, policy)
    write_json(event_path, event)
    state_path.write_text(state_text, encoding="utf-8")
    before_policy = policy_path.read_bytes()
    before_event = event_path.read_bytes()
    before_state = state_path.read_bytes()
    result = subprocess.run(
        [
            str(BIN),
            "--policy",
            str(policy_path),
            "--event",
            str(event_path),
            "--state",
            str(state_path),
            "--out",
            str(report_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert policy_path.read_bytes() == before_policy
    assert event_path.read_bytes() == before_event
    return result, report_path, state_path, before_state


def run_gate_text(tmp_path, policy_text, event_text, state_text):
    policy_path = tmp_path / "policy.json"
    event_path = tmp_path / "event.json"
    state_path = tmp_path / "state.json"
    report_path = tmp_path / "report.json"
    policy_path.write_text(policy_text, encoding="utf-8")
    event_path.write_text(event_text, encoding="utf-8")
    state_path.write_text(state_text, encoding="utf-8")
    before_policy = policy_path.read_bytes()
    before_event = event_path.read_bytes()
    before_state = state_path.read_bytes()
    result = subprocess.run(
        [
            str(BIN),
            "--policy",
            str(policy_path),
            "--event",
            str(event_path),
            "--state",
            str(state_path),
            "--out",
            str(report_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert policy_path.read_bytes() == before_policy
    assert event_path.read_bytes() == before_event
    return result, report_path, state_path, before_state


def credential_state(counter=5, extra=False):
    credentials = [
        {"credential_id": b"cred-1".hex(), "aaguid": AAGUID, "sign_count": counter}
    ]
    if extra:
        credentials.append(
            {"credential_id": b"other".hex(), "aaguid": AAGUID, "sign_count": 3}
        )
    seed = audit_entry(
        0,
        sha_hex(b"seeded credential registry"),
        ZERO_HASH,
        b"cred-1".hex(),
        counter,
        "registered",
    )
    return json.dumps({"credentials": credentials, "audit": [seed]}, separators=(",", ":"))


def empty_state():
    return '{"credentials":[],"audit":[]}'


def test_valid_registration_writes_report_and_state(tmp_path):
    """A conforming attested registration is admitted and persisted."""
    event = registration_event()
    event["credential_id"] = event["credential_id"].upper()
    policy = policy_obj()
    policy["trusted_aaguids"][0]["aaguid"] = policy["trusted_aaguids"][0]["aaguid"].upper()
    result, report_path, state_path, _ = run_gate(
        tmp_path, policy, event, empty_state()
    )
    assert result.returncode == 0, result.stderr
    report = read_report(report_path)
    assert report["admitted"] is True
    assert report["status"] == "registered"
    assert report["credential_id"] == b"cred-1".hex()
    assert report["credential_id"] == report["credential_id"].lower()
    assert report["sign_count"] == 1
    expected_entry = audit_entry(
        0,
        sha_hex(json.dumps(event, separators=(",", ":")).encode()),
        ZERO_HASH,
        b"cred-1".hex(),
        1,
        "registered",
    )
    assert report["audit_hash"] == expected_entry["entry_hash"]
    assert report["violations"] == []
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["credentials"] == [
        {"credential_id": b"cred-1".hex(), "aaguid": AAGUID, "sign_count": 1}
    ]
    assert state["audit"] == [expected_entry]
    assert state["credentials"][0]["aaguid"] == state["credentials"][0]["aaguid"].lower()


def test_registration_enforces_rp_hash_uv_and_state_stability(tmp_path):
    """Policy-bound RP identity and user verification are enforced before state changes."""
    for event in [
        registration_event(rp_id="evil.example"),
        registration_event(flags=0x01),
    ]:
        result, report_path, state_path, before_state = run_gate(
            tmp_path, policy_obj(), event, empty_state()
        )
        assert result.returncode == 0
        report = read_report(report_path)
        assert report["admitted"] is False
        assert report["status"] == "rejected"
        assert state_path.read_bytes() == before_state


@pytest.mark.parametrize(
    "event",
    [
        registration_event(challenge="not-issued"),
        registration_event(origin="https://phish.example"),
        registration_event(client_kind="webauthn.get"),
        registration_event(flags=0x04),
    ],
)
def test_registration_rejects_client_policy_and_presence_violations(tmp_path, event):
    """Client-data policy and user-present requirements are enforced as denials."""
    result, report_path, state_path, before_state = run_gate(
        tmp_path, policy_obj(), event, empty_state()
    )
    assert result.returncode == 0
    report = read_report(report_path)
    assert report["admitted"] is False
    assert report["status"] == "rejected"
    assert state_path.read_bytes() == before_state


def test_registration_rejects_attestation_trust_identity_and_signature_errors(tmp_path):
    """Attestation trust, AAGUID matching, and signature validation affect admission."""
    untrusted_policy = policy_obj()
    untrusted_policy["trusted_aaguids"][0]["aaguid"] = "10" * 16
    mismatched = registration_event(aaguid="22" * 16)
    bad_signature = registration_event()
    bad_signature["attestation_object"] = flip_last_hex_digit(
        bad_signature["attestation_object"]
    )
    for policy, event in [
        (untrusted_policy, registration_event()),
        (policy_obj(), mismatched),
        (policy_obj(), bad_signature),
    ]:
        result, report_path, state_path, before_state = run_gate(
            tmp_path, policy, event, empty_state()
        )
        assert result.returncode == 0
        report = read_report(report_path)
        assert report["admitted"] is False
        assert report["status"] == "rejected"
        assert state_path.read_bytes() == before_state


def test_authentication_signature_binds_client_data(tmp_path):
    """The assertion signature covers the decoded client data used for the event."""
    state_text = credential_state(counter=5)
    event = authentication_event(
        challenge="challenge-a", prev_hash=current_audit_hash(state_text)
    )
    changed_client = client_data("webauthn.get", challenge="challenge-b")
    event["client_data_json"] = b64url(changed_client)
    result, report_path, state_path, before_state = run_gate(
        tmp_path, policy_obj(), event, state_text
    )
    assert result.returncode == 0
    report = read_report(report_path)
    assert report["admitted"] is False
    assert report["status"] == "rejected"
    assert state_path.read_bytes() == before_state


def test_authentication_signature_binds_current_audit_hash(tmp_path):
    """Assertion signatures are tied to the verified audit-chain head in state."""
    state_text = credential_state(counter=5)
    event = authentication_event(counter=6, prev_hash=ZERO_HASH)
    result, report_path, state_path, before_state = run_gate(
        tmp_path, policy_obj(), event, state_text
    )
    assert result.returncode == 0
    report = read_report(report_path)
    assert report["admitted"] is False
    assert report["status"] == "rejected"
    assert report["audit_hash"] == current_audit_hash(state_text)
    assert state_path.read_bytes() == before_state


def test_authentication_enforces_rp_hash_and_user_flags(tmp_path):
    """Authentication events must pass the same RP and user-presence gates as registrations."""
    for event in [
        authentication_event(rp_id="evil.example"),
        authentication_event(flags=0x04),
        authentication_event(flags=0x01),
    ]:
        result, report_path, state_path, before_state = run_gate(
            tmp_path, policy_obj(), event, credential_state(counter=5)
        )
        assert result.returncode == 0
        report = read_report(report_path)
        assert report["admitted"] is False
        assert report["status"] == "rejected"
        assert state_path.read_bytes() == before_state


def test_duplicate_registration_and_unknown_authentication_are_denied(tmp_path):
    """Credential admission uses the persisted registry for both event kinds."""
    duplicate, duplicate_report, duplicate_state, duplicate_before = run_gate(
        tmp_path, policy_obj(), registration_event(), credential_state(counter=4)
    )
    assert duplicate.returncode == 0
    duplicate_body = read_report(duplicate_report)
    assert duplicate_body["admitted"] is False
    assert duplicate_body["status"] == "rejected"
    assert duplicate_state.read_bytes() == duplicate_before

    unknown_event = authentication_event(credential_id=b"missing", counter=9)
    unknown, unknown_report, unknown_state, unknown_before = run_gate(
        tmp_path, policy_obj(), unknown_event, credential_state(counter=4)
    )
    assert unknown.returncode == 0
    unknown_body = read_report(unknown_report)
    assert unknown_body["admitted"] is False
    assert unknown_body["status"] == "rejected"
    assert unknown_state.read_bytes() == unknown_before


def test_sign_counter_persists_monotonically_and_preserves_peers(tmp_path):
    """Authentication counters must advance strictly while unrelated credentials stay intact."""
    peer_state = credential_state(counter=7, extra=True)
    rejected, rejected_report, rejected_state, rejected_before = run_gate(
        tmp_path,
        policy_obj(),
        authentication_event(counter=7, prev_hash=current_audit_hash(peer_state)),
        peer_state,
    )
    assert rejected.returncode == 0
    assert read_report(rejected_report)["admitted"] is False
    assert rejected_state.read_bytes() == rejected_before

    accepted, report_path, state_path, _ = run_gate(
        tmp_path,
        policy_obj(),
        authentication_event(counter=8, prev_hash=current_audit_hash(peer_state)),
        peer_state,
    )
    assert accepted.returncode == 0, accepted.stderr
    report = read_report(report_path)
    assert report["admitted"] is True
    assert report["status"] == "authenticated"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["credentials"] == [
        {"credential_id": b"cred-1".hex(), "aaguid": AAGUID, "sign_count": 8},
        {"credential_id": b"other".hex(), "aaguid": AAGUID, "sign_count": 3},
    ]
    assert len(state["audit"]) == 2
    assert state["audit"][1]["prev_hash"] == current_audit_hash(peer_state)
    assert state["audit"][1]["entry_hash"] == report["audit_hash"]


def test_malformed_authenticator_data_exits_two_and_keeps_state(tmp_path):
    """Truncated authenticator data is reported as malformed without touching state."""
    event = registration_event()
    event["authenticator_data"] = "00ff"
    result, report_path, state_path, before_state = run_gate(
        tmp_path, policy_obj(), event, empty_state()
    )
    assert result.returncode == 2
    report = read_report(report_path)
    assert report["admitted"] is False
    assert report["status"] == "malformed"
    assert state_path.read_bytes() == before_state


def test_malformed_cbor_json_hex_base64_fields_and_state_exit_two(tmp_path):
    """Malformed encodings, missing fields, and unusable state are reported without writes."""
    policy_text = json.dumps(policy_obj(), separators=(",", ":"))
    valid_event_text = json.dumps(registration_event(), separators=(",", ":"))
    duplicate_key_event = '{"kind":"registration","kind":"authentication","credential_id":"637265642d31","client_data_json":"e30","authenticator_data":"00","attestation_object":"00"}'
    plus_base64_event = registration_event()
    plus_base64_event["client_data_json"] = "+___"
    duplicate_cbor_event = registration_event()
    duplicate_cbor_event["attestation_object"] = cbor_map(
        [
            ("fmt", "packed"),
            ("fmt", "packed"),
            ("aaguid", bytes.fromhex(AAGUID)),
            ("credential_id", b"cred-1"),
            ("nonce", b"registration nonce"),
            ("sig", b"\x00" * 32),
        ]
    ).hex()
    non_map_key_event = registration_event()
    bad_ad = auth_data(
        1,
        credential_id=b"cred-1",
        include_attested=True,
        credential_public_key=b"\x80",
    )
    non_map_key_event["authenticator_data"] = bad_ad.hex()
    malformed_cases = [
        (policy_text, "{", empty_state()),
        (policy_text, duplicate_key_event, empty_state()),
        (policy_text, json.dumps(plus_base64_event, separators=(",", ":")), empty_state()),
        (policy_text, json.dumps(duplicate_cbor_event, separators=(",", ":")), empty_state()),
        (policy_text, json.dumps(non_map_key_event, separators=(",", ":")), empty_state()),
        (
            policy_text,
            valid_event_text.replace('"credential_id"', '"missing"', 1),
            empty_state(),
        ),
        (
            policy_text,
            valid_event_text.replace(registration_event()["authenticator_data"], "abcx"),
            empty_state(),
        ),
        (
            policy_text,
            valid_event_text.replace(registration_event()["client_data_json"], "@@@"),
            empty_state(),
        ),
        (
            policy_text,
            valid_event_text.replace(registration_event()["attestation_object"], "ff"),
            empty_state(),
        ),
        (policy_text, valid_event_text, '{"credentials":"not-array","audit":[]}'),
        (
            policy_text,
            valid_event_text,
            json.dumps(
                {
                    "credentials": [],
                    "audit": [
                        {
                            "seq": 0,
                            "event_hash": ZERO_HASH,
                            "status": "registered",
                            "credential_id": b"cred-1".hex(),
                            "sign_count": 1,
                            "prev_hash": ZERO_HASH,
                            "entry_hash": ZERO_HASH,
                        }
                    ],
                },
                separators=(",", ":"),
            ),
        ),
    ]
    for index, (policy_raw, event_raw, state_raw) in enumerate(malformed_cases):
        case_dir = tmp_path / f"case-{index}"
        case_dir.mkdir()
        result, report_path, state_path, before_state = run_gate_text(
            case_dir, policy_raw, event_raw, state_raw
        )
        assert result.returncode == 2
        report = read_report(report_path)
        assert report["admitted"] is False
        assert report["status"] == "malformed"
        assert state_path.read_bytes() == before_state
