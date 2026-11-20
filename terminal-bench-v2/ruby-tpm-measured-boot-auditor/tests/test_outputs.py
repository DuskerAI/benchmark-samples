import hashlib
import json
import struct
import subprocess

TOOL = "/opt/measured_boot_auditor/bin/boot_audit"
ALG = {"sha1": 0x0004, "sha256": 0x000B, "sha384": 0x000C}
SIZE = {"sha1": 20, "sha256": 32, "sha384": 48}
VAR_BOOT = 0x80000002
VAR_AUTH = 0x800000E0
SEP = 0x00000004
GUID = bytes.fromhex("8be4df61 93ca 11d2 aa0d00e098032b8c".replace(" ", ""))


def digest(bank, data):
    return hashlib.new(bank, data).digest()


def extend(bank, old, event_digest):
    return digest(bank, old + event_digest)


def record(layout, pcr, event_type, payload, banks=("sha1", "sha256"), override=None):
    head = struct.pack("<BII", layout, pcr, event_type)
    override = override or {}
    if layout == 1:
        d = override.get("sha1", digest("sha1", payload))
        return head + d + struct.pack("<I", len(payload)) + payload
    parts = []
    for bank in banks:
        parts.append(struct.pack("<H", ALG[bank]))
        parts.append(override.get(bank, digest(bank, payload)))
    return head + struct.pack("<I", len(banks)) + b"".join(parts) + struct.pack("<I", len(payload)) + payload


def var_payload(name, data, guid=GUID):
    encoded = name.encode("utf-16le")
    return guid + struct.pack("<HI", len(encoded), len(data)) + encoded + data


SIG_TYPE = bytes.fromhex("a5c059a1 94e4 4aa7 87b5ab155c2bf072".replace(" ", ""))
OWNER = bytes.fromhex("00112233445566778899aabbccddeeff")


def sigdb(*items, owner=OWNER):
    records = []
    for item in items:
        signature_size = 16 + len(item)
        list_size = 28 + signature_size
        records.append(SIG_TYPE + struct.pack("<III", list_size, 0, signature_size) + owner + item)
    return b"".join(records)


def entry_hashes(value):
    hashes = []
    pos = 0
    while pos < len(value):
        assert pos + 28 <= len(value), "truncated signature-list header"
        list_size, header_size, signature_size = struct.unpack_from("<III", value, pos + 16)
        assert list_size >= 28 + header_size, "invalid signature-list size"
        assert signature_size > 16, "signature entry lacks authority bytes"
        end = pos + list_size
        assert end <= len(value), "truncated signature list"
        sig_pos = pos + 28 + header_size
        assert (end - sig_pos) % signature_size == 0, "unaligned signature entries"
        while sig_pos < end:
            hashes.append(hashlib.sha256(value[sig_pos + 16:sig_pos + signature_size]).hexdigest())
            sig_pos += signature_size
        pos = end
    return hashes


def replay(events, banks):
    pcrs = {bank: {} for bank in banks}
    event_violations = []
    variables = {}
    for ordinal, item in enumerate(events):
        for bank, logged in item["digests"].items():
            if bank in banks and digest(bank, item["payload"]) != logged:
                event_violations.append({"code": "digest-mismatch", "event": ordinal, "bank": bank})
        if item["type"] == SEP and item["payload"] not in (b"\x00\x00\x00\x00", b"\xff\xff\xff\xff"):
            event_violations.append({"code": "separator", "event": ordinal, "pcr": item["pcr"]})
        if item["type"] in (VAR_BOOT, VAR_AUTH):
            payload = item["payload"]
            assert len(payload) >= 22, "truncated variable payload"
            name_len, data_len = struct.unpack_from("<HI", payload, 16)
            assert name_len % 2 == 0, "odd UTF-16LE name length"
            assert 22 + name_len + data_len == len(payload), "variable length mismatch"
            name = payload[22:22 + name_len].decode("utf-16le").casefold()
            key = (payload[:16].hex(), name)
            variables[key] = payload[22 + name_len:22 + name_len + data_len]
        for bank in banks:
            logged = item["digests"].get(bank)
            if logged is None:
                continue
            previous = pcrs[bank].get(item["pcr"], b"\x00" * SIZE[bank])
            pcrs[bank][item["pcr"]] = extend(bank, previous, logged)
    return pcrs, variables, event_violations


def expected_report(events, policy):
    pcrs, variables, violations = replay(events, policy["banks"])
    if "secure_boot" in policy:
        found = None
        for (_, name), value in variables.items():
            if name == "secureboot":
                found = value.hex()
        if found != policy["secure_boot"].lower():
            violations.append({"code": "secure-boot", "name": "SecureBoot"})
    forbidden = {item.lower() for item in policy.get("forbidden_authorities", [])}
    required = {item.lower() for item in policy.get("required_authorities", [])}
    db_hashes = []
    dbx_hashes = []
    for (_, name), value in variables.items():
        if name == "db":
            db_hashes = entry_hashes(value)
        elif name == "dbx":
            dbx_hashes = entry_hashes(value)
    if forbidden.intersection(db_hashes):
        violations.append({"code": "forbidden-authority", "name": "db"})
    for item in sorted(required):
        if item not in db_hashes:
            violations.append({"code": "required-authority", "name": "db"})
    if required.intersection(dbx_hashes):
        violations.append({"code": "revoked-authority", "name": "dbx"})
    for want in policy.get("required_events", []):
        if not any(
            item["pcr"] == want["pcr"]
            and item["type"] == want["event_type"]
            and want["bank"] in item["digests"]
            and hashlib.sha256(item["payload"]).hexdigest() == want["payload_sha256"].lower()
            for item in events
        ):
            violations.append(
                {
                    "code": "missing-event",
                    "pcr": want["pcr"],
                    "event_type": want["event_type"],
                    "bank": want["bank"],
                    "payload_sha256": want["payload_sha256"].lower(),
                }
            )
    for bank, values in policy.get("expected_pcrs", {}).items():
        for pcr_text, expected in sorted(values.items(), key=lambda kv: int(kv[0])):
            got = pcrs.get(bank, {}).get(int(pcr_text))
            if got is None or got.hex() != expected.lower():
                violations.append({"code": "pcr-mismatch", "bank": bank, "pcr": int(pcr_text)})
    rendered = {
        bank: {str(pcr): value.hex() for pcr, value in sorted(vals.items())}
        for bank, vals in pcrs.items()
        if vals
    }
    return {"ok": not violations, "pcrs": rendered, "violations": violations}


def materialize(tmp_path, name, events, policy):
    case_dir = tmp_path / name
    case_dir.mkdir()
    eventlog = b"".join(item["record"] for item in events)
    (case_dir / "eventlog.bin").write_bytes(eventlog)
    (case_dir / "policy.json").write_text(json.dumps(policy, sort_keys=True) + "\n")
    return case_dir


def run_case(tmp_path, name, events, policy):
    case_dir = materialize(tmp_path, name, events, policy)
    out = tmp_path / f"{name}.json"
    result = subprocess.run([TOOL, str(case_dir), str(out)], text=True, capture_output=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr + result.stdout
    assert out.read_bytes().endswith(b"\n")
    return json.loads(out.read_text())


def run_raw_case(tmp_path, name, eventlog, policy):
    case_dir = tmp_path / name
    case_dir.mkdir()
    (case_dir / "eventlog.bin").write_bytes(eventlog)
    (case_dir / "policy.json").write_text(json.dumps(policy, sort_keys=True) + "\n")
    out = tmp_path / f"{name}.json"
    return subprocess.run([TOOL, str(case_dir), str(out)], text=True, capture_output=True, timeout=20, check=False), out


def event(layout, pcr, event_type, payload, banks=("sha1", "sha256"), override=None):
    digests = {bank: (override or {}).get(bank, digest(bank, payload)) for bank in banks}
    if layout == 1:
        digests = {"sha1": digests["sha1"]}
    return {
        "record": record(layout, pcr, event_type, payload, banks=banks, override=override),
        "pcr": pcr,
        "type": event_type,
        "payload": payload,
        "digests": digests,
    }


def test_accepts_generated_attestation(tmp_path):
    """Accepts mixed layout-1/layout-2 logs, per-bank replay, and final SecureBoot state."""
    events = [
        event(2, 0, 0x00000003, b"platform-firmware" + bytes(range(9)), ("sha1", "sha256")),
        event(1, 0, 0x0000000D, b"option-rom" + b"\x10" * 17, ("sha1",)),
        event(2, 7, SEP, b"\x00\x00\x00\x00", ("sha256", "sha1")),
        event(2, 7, VAR_BOOT, var_payload("SecureBoot", b"\x00"), ("sha1", "sha256")),
        event(2, 7, VAR_BOOT, var_payload("secureboot", b"\x01"), ("sha256",)),
        event(2, 7, VAR_AUTH, var_payload("db", sigdb(b"vendor-a", b"vendor-b")), ("sha256",)),
    ]
    pcrs, _, _ = replay(events, ["sha1", "sha256"])
    policy = {
        "banks": ["sha1", "sha256"],
        "secure_boot": "01",
        "forbidden_authorities": [hashlib.sha256(b"not-present").hexdigest()],
        "expected_pcrs": {
            "sha1": {"0": pcrs["sha1"][0].hex(), "7": pcrs["sha1"][7].hex()},
            "sha256": {"0": pcrs["sha256"][0].hex(), "7": pcrs["sha256"][7].hex()},
        },
    }
    assert run_case(tmp_path, "case_a", events, policy) == expected_report(events, policy)


def test_rejects_policy_and_log_violations(tmp_path):
    """Reports separator, digest-mismatch, and db entry blob forbidden-authority hits."""
    bad_payload = b"late-driver" + b"\x99" * 13
    db_data = sigdb(b"allowed authority", b"revoked authority")
    events = [
        event(2, 2, 0x00000005, b"loader" + b"\x01" * 12, ("sha256", "sha1")),
        event(2, 7, SEP, b"\x01\x00\x00\x00", ("sha1", "sha256")),
        event(2, 7, VAR_BOOT, var_payload("SecureBoot", b"\x01"), ("sha256", "sha1")),
        event(2, 7, VAR_AUTH, var_payload("db", db_data), ("sha1", "sha256")),
        event(2, 2, 0x0000000D, bad_payload, ("sha1", "sha256"), {"sha256": b"\x42" * 32}),
    ]
    pcrs, _, _ = replay(events, ["sha1", "sha256"])
    policy = {
        "banks": ["sha1", "sha256"],
        "secure_boot": "01",
        "forbidden_authorities": [hashlib.sha256(b"revoked authority").hexdigest()],
        "expected_pcrs": {"sha256": {"2": pcrs["sha256"][2].hex(), "7": pcrs["sha256"][7].hex()}},
    }
    report = run_case(tmp_path, "case_b", events, policy)
    assert report == expected_report(events, policy)
    assert [v["code"] for v in report["violations"]] == ["separator", "digest-mismatch", "forbidden-authority"]


def test_bank_selection_and_final_state(tmp_path):
    """Uses only requested banks while preserving case-insensitive final-write variables."""
    events = [
        event(1, 4, 0x0000000D, b"legacy-only-event" + b"\x03" * 8, ("sha1",)),
        event(2, 4, 0x0000000D, b"kernel-image" + b"\x04" * 11, ("sha256",)),
        event(2, 7, VAR_BOOT, var_payload("SECUREBOOT", b"\x00"), ("sha1",)),
        event(2, 7, VAR_BOOT, var_payload("SecureBoot", b"\x01"), ("sha256",)),
    ]
    pcrs, _, _ = replay(events, ["sha256"])
    policy = {
        "banks": ["sha256"],
        "secure_boot": "01",
        "expected_pcrs": {"sha256": {"4": pcrs["sha256"][4].hex(), "7": pcrs["sha256"][7].hex()}},
    }
    report = run_case(tmp_path, "case_c", events, policy)
    assert report == expected_report(events, policy)
    assert "sha1" not in report["pcrs"]


def test_authority_policy_uses_final_db_and_dbx_state(tmp_path):
    """Authority policy is evaluated from final variable state, not any earlier db write."""
    old_forbidden = b"retired test authority"
    required = b"required production authority" + bytes(range(12))
    missing = b"missing authority"
    allowed = b"allowed authority" + b"\x81" * 9
    events = [
        event(2, 0, 0x00000003, b"firmware-volume" + b"\x41" * 19, ("sha1", "sha256")),
        event(2, 7, VAR_AUTH, var_payload("db", sigdb(old_forbidden)), ("sha256", "sha1")),
        event(2, 7, VAR_AUTH, var_payload("DB", sigdb(required, allowed)), ("sha1", "sha256")),
        event(2, 7, VAR_AUTH, var_payload("dbx", sigdb(required)), ("sha256",)),
    ]
    pcrs, _, _ = replay(events, ["sha1", "sha256"])
    policy = {
        "banks": ["sha1", "sha256"],
        "forbidden_authorities": [hashlib.sha256(old_forbidden).hexdigest()],
        "required_authorities": [hashlib.sha256(required).hexdigest(), hashlib.sha256(missing).hexdigest()],
        "expected_pcrs": {
            "sha1": {"0": pcrs["sha1"][0].hex(), "7": pcrs["sha1"][7].hex()},
            "sha256": {"0": pcrs["sha256"][0].hex(), "7": pcrs["sha256"][7].hex()},
        },
    }
    report = run_case(tmp_path, "case_authority", events, policy)
    assert report == expected_report(events, policy)
    assert [v["code"] for v in report["violations"]] == ["required-authority", "revoked-authority"]


def test_requested_banks_drive_digest_checks_and_extensions(tmp_path):
    """A bad unrequested digest is ignored, while a bad requested digest still extends PCRs as logged."""
    ignored_sha1_payload = b"shim-loader" + b"\x51" * 17
    bad_sha256_payload = b"kernel-command-line" + b"\x72" * 11
    logged_bad_sha256 = b"\xa5" * 32
    events = [
        event(2, 2, 0x0000000D, ignored_sha1_payload, ("sha1", "sha256"), {"sha1": b"\x9c" * 20}),
        event(2, 2, 0x0000000D, bad_sha256_payload, ("sha256", "sha1"), {"sha256": logged_bad_sha256}),
        event(2, 2, SEP, b"\xff\xff\xff\xff", ("sha256",)),
    ]
    pcrs, _, _ = replay(events, ["sha256"])
    policy = {
        "banks": ["sha256"],
        "expected_pcrs": {"sha256": {"2": pcrs["sha256"][2].hex()}},
    }
    report = run_case(tmp_path, "case_digest_scope", events, policy)
    assert report == expected_report(events, policy)
    assert report["violations"] == [{"code": "digest-mismatch", "event": 1, "bank": "sha256"}]


def test_rejects_duplicate_algorithm_and_malformed_authority_state(tmp_path):
    """Strict binary validation rejects layout-2 duplicate algorithms and malformed authority entries."""
    duplicate_alg_record = record(2, 7, SEP, b"\x00\x00\x00\x00", ("sha256", "sha256"))
    result, out = run_raw_case(tmp_path, "dup_alg", duplicate_alg_record, {"banks": ["sha256"]})
    assert result.returncode != 0
    if out.exists():
        assert json.loads(out.read_text()).get("ok") is not True

    malformed_db = var_payload("db", SIG_TYPE + struct.pack("<III", 34, 0, 21) + OWNER + b"abc")
    malformed_log = record(2, 7, VAR_AUTH, malformed_db, ("sha256",))
    policy = {
        "banks": ["sha256"],
        "forbidden_authorities": [hashlib.sha256(b"abc").hexdigest()],
    }
    result, out = run_raw_case(tmp_path, "bad_db", malformed_log, policy)
    assert result.returncode != 0
    if out.exists():
        assert json.loads(out.read_text()).get("ok") is not True

    malformed_dbx = var_payload("dbx", SIG_TYPE + struct.pack("<III", 40, 0, 20) + OWNER + b"xy")
    malformed_dbx_log = record(2, 7, VAR_AUTH, malformed_dbx, ("sha256",))
    result, out = run_raw_case(tmp_path, "bad_dbx_without_policy_list", malformed_dbx_log, {"banks": ["sha256"]})
    assert result.returncode != 0
    if out.exists():
        assert json.loads(out.read_text()).get("ok") is not True


def test_final_authority_lists_are_eagerly_validated(tmp_path):
    """Final db and dbx entry lists are parsed even when no authority policy references them."""
    good_db = sigdb(b"current db authority")
    good_dbx = sigdb(b"current dbx authority")
    overwritten_bad_db = SIG_TYPE + struct.pack("<III", 45, 0, 25) + OWNER + b"abc"
    events = [
        event(2, 7, VAR_AUTH, var_payload("db", overwritten_bad_db), ("sha256",)),
        event(2, 7, VAR_AUTH, var_payload("DB", good_db), ("sha256",)),
        event(2, 7, VAR_AUTH, var_payload("DbX", good_dbx), ("sha256",)),
        event(2, 7, VAR_BOOT, var_payload("SecureBoot", b"\x01"), ("sha256",)),
    ]
    pcrs, _, _ = replay(events, ["sha256"])
    policy = {
        "banks": ["sha256"],
        "secure_boot": "01",
        "expected_pcrs": {"sha256": {"7": pcrs["sha256"][7].hex()}},
    }
    assert run_case(tmp_path, "authority_overwrite", events, policy) == expected_report(events, policy)

    malformed_final_db = record(
        2,
        7,
        VAR_AUTH,
        var_payload("db", SIG_TYPE + struct.pack("<III", 42, 0, 21) + OWNER + b"abc"),
        ("sha256",),
    )
    result, out = run_raw_case(tmp_path, "bad_final_db_without_policy", malformed_final_db, {"banks": ["sha256"]})
    assert result.returncode != 0
    if out.exists():
        assert json.loads(out.read_text()).get("ok") is not True


def test_violation_entries_are_json_objects(tmp_path):
    """Policy and PCR failures are emitted as structured violation objects, not strings."""
    forbidden = b"forbidden authority"
    required = b"required authority"
    missing = b"missing authority"
    events = [
        event(2, 7, VAR_BOOT, var_payload("SecureBoot", b"\x01"), ("sha256",)),
        event(2, 7, VAR_AUTH, var_payload("db", sigdb(forbidden, required)), ("sha256",)),
        event(2, 7, VAR_AUTH, var_payload("dbx", sigdb(required)), ("sha256",)),
    ]
    policy = {
        "banks": ["sha256"],
        "secure_boot": "00",
        "forbidden_authorities": [hashlib.sha256(forbidden).hexdigest()],
        "required_authorities": [hashlib.sha256(required).hexdigest(), hashlib.sha256(missing).hexdigest()],
        "expected_pcrs": {"sha256": {"7": "00" * 32}},
    }
    report = run_case(tmp_path, "structured_violations", events, policy)
    assert report == expected_report(events, policy)
    assert report["violations"] == [
        {"code": "secure-boot", "name": "SecureBoot"},
        {"code": "forbidden-authority", "name": "db"},
        {"code": "required-authority", "name": "db"},
        {"code": "revoked-authority", "name": "dbx"},
        {"code": "pcr-mismatch", "bank": "sha256", "pcr": 7},
    ]
    assert all(isinstance(item, dict) for item in report["violations"])


def test_sha384_signature_database_and_required_events(tmp_path):
    """SHA-384 replay, EFI signature-list authority hashes, and required event policy interact."""
    release_payload = b"stage2-loader-release" + bytes(range(16))
    recovery_payload = b"stage2-loader-recovery" + bytes(range(7))
    forbidden = b"blocked shim certificate" + bytes(range(5))
    required = b"production signing certificate" + b"\x42" * 9
    events = [
        event(2, 0, 0x00000003, b"firmware-volume" + b"\x10" * 24, ("sha384", "sha256")),
        event(2, 4, 0x0000000D, release_payload, ("sha384", "sha256")),
        event(2, 4, 0x0000000D, recovery_payload, ("sha384",)),
        event(2, 7, VAR_AUTH, var_payload("db", sigdb(forbidden, required)), ("sha384", "sha256")),
    ]
    pcrs, _, _ = replay(events, ["sha384", "sha256"])
    missing_payload = b"debug-loader" + b"\x33" * 11
    policy = {
        "banks": ["sha384", "sha256"],
        "forbidden_authorities": [hashlib.sha256(forbidden).hexdigest()],
        "required_authorities": [hashlib.sha256(required).hexdigest()],
        "required_events": [
            {
                "pcr": 4,
                "event_type": 0x0000000D,
                "bank": "sha384",
                "payload_sha256": hashlib.sha256(release_payload).hexdigest(),
            },
            {
                "pcr": 4,
                "event_type": 0x0000000D,
                "bank": "sha256",
                "payload_sha256": hashlib.sha256(missing_payload).hexdigest(),
            },
        ],
        "expected_pcrs": {"sha384": {"4": pcrs["sha384"][4].hex()}, "sha256": {"0": pcrs["sha256"][0].hex()}},
    }
    report = run_case(tmp_path, "sha384_sigdb_required", events, policy)
    assert report == expected_report(events, policy)
    assert [item["code"] for item in report["violations"]] == ["forbidden-authority", "missing-event"]


def test_rejects_truncated_event_log(tmp_path):
    """A truncated record must fail before emitting a successful attestation report."""
    case_dir = tmp_path / "truncated"
    case_dir.mkdir()
    (case_dir / "eventlog.bin").write_bytes(struct.pack("<BII", 2, 7, SEP) + struct.pack("<I", 1))
    (case_dir / "policy.json").write_text(json.dumps({"banks": ["sha256"]}) + "\n")
    out = tmp_path / "truncated.json"

    result = subprocess.run([TOOL, str(case_dir), str(out)], text=True, capture_output=True, timeout=20, check=False)

    assert result.returncode != 0
    if out.exists():
        assert json.loads(out.read_text()).get("ok") is not True
