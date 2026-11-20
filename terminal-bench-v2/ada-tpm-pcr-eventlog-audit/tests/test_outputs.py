import hashlib
import json
import os
import pwd
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path("/app/pcrgate")
BIN = ROOT / "bin" / "pcrgate"
BUILD_SCRIPT = ROOT / "scripts" / "build.sh"
RUNUSER = Path("/usr/sbin/runuser")
CANDIDATE_USER = "agent"


def h(alg, data):
    if alg == 1:
        return hashlib.sha1(data).digest()
    if alg == 2:
        return hashlib.sha256(data).digest()
    raise AssertionError(alg)


def extend(old, digest, alg):
    return h(alg, old + digest)


def record(pcr, event_type, payload, algs=(1, 2)):
    payload = payload.encode()
    parts = [struct.pack("<IIB", pcr, event_type, len(algs))]
    for alg in algs:
        digest = h(alg, payload)
        parts.append(struct.pack("BB", alg, len(digest)))
        parts.append(digest)
    parts.append(struct.pack("<H", len(payload)))
    parts.append(payload)
    return b"".join(parts)


def record_with_digest_specs(pcr, event_type, payload, specs):
    payload = payload.encode()
    parts = [struct.pack("<IIB", pcr, event_type, len(specs))]
    for alg, digest in specs:
        parts.append(struct.pack("BB", alg, len(digest)))
        parts.append(digest)
    parts.append(struct.pack("<H", len(payload)))
    parts.append(payload)
    return b"".join(parts)


def expected_pcr(events):
    banks = {1: {i: b"\x00" * 20 for i in range(24)}, 2: {i: b"\x00" * 32 for i in range(24)}}
    for pcr, _typ, payload, algs in events:
        payload_b = payload.encode()
        for alg in algs:
            banks[alg][pcr] = extend(banks[alg][pcr], h(alg, payload_b), alg)
    return {
        "sha1": {str(k): v.hex() for k, v in banks[1].items()},
        "sha256": {str(k): v.hex() for k, v in banks[2].items()},
    }


def build_verifier_binary(tmp_path):
    proc = subprocess.run(
        [str(BUILD_SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert BIN.exists(), proc.stdout + proc.stderr
    assert os.access(BIN, os.X_OK), f"{BIN} is not executable"
    assert BIN.read_bytes()[:4] == b"\x7fELF", "build.sh must produce a native ELF binary"
    exe = tmp_path / "verifier-built-pcrgate"
    shutil.copy2(BIN, exe)
    exe.chmod(0o755)
    return exe


def candidate_account():
    try:
        return pwd.getpwnam(CANDIDATE_USER)
    except KeyError:
        return None


def test_build_script_produces_native_pcrgate_binary():
    """The public build script builds the native Ada gate binary at the required path."""
    assert BUILD_SCRIPT.exists(), f"missing build script: {BUILD_SCRIPT}"
    proc = subprocess.run(
        [str(BUILD_SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert BIN.exists(), proc.stdout + proc.stderr
    assert os.access(BIN, os.X_OK), f"{BIN} is not executable"
    assert BIN.read_bytes()[:4] == b"\x7fELF", "build.sh must produce a native ELF binary"


def make_candidate_workspace(path):
    account = candidate_account()
    if os.geteuid() != 0 or account is None:
        return
    for parent in Path(path).resolve().parents:
        if parent == Path("/tmp"):
            break
        os.chmod(parent, os.stat(parent).st_mode | 0o111)
    for current, dirs, files in os.walk(path):
        os.chown(current, account.pw_uid, account.pw_gid)
        os.chmod(current, 0o700)
        for dirname in dirs:
            child = Path(current) / dirname
            os.chown(child, account.pw_uid, account.pw_gid)
            os.chmod(child, 0o700)
        for filename in files:
            child = Path(current) / filename
            mode = child.stat().st_mode
            os.chown(child, account.pw_uid, account.pw_gid)
            os.chmod(child, 0o700 if mode & 0o111 else 0o600)


def run_candidate_binary(argv, tmp_path, env):
    if os.geteuid() != 0:
        return subprocess.run(
            argv,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    account = candidate_account()
    assert account is not None, f"missing unprivileged {CANDIDATE_USER} account"
    assert RUNUSER.exists(), "runuser is required to execute verifier-built candidate binaries unprivileged"
    make_candidate_workspace(tmp_path)

    tests_dir = Path("/tests")
    original_mode = None
    if tests_dir.exists():
        original_mode = tests_dir.stat().st_mode & 0o777
        os.chmod(tests_dir, 0o700)
    try:
        return subprocess.run(
            [str(RUNUSER), "-u", CANDIDATE_USER, "--", *argv],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        if original_mode is not None:
            os.chmod(tests_dir, original_mode)


def run_gate(tmp_path, events_by_file, policy):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    for name, events in events_by_file.items():
        blob = bytearray()
        for ev in events:
            if isinstance(ev, bytes):
                blob += ev
            else:
                blob += record(*ev)
        (events_dir / name).write_bytes(blob)
    policy_path = tmp_path / "policy.json"
    out_path = tmp_path / "report.json"
    policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
    verifier_bin = build_verifier_binary(tmp_path)
    hidden_agent_bin = tmp_path / "agent-controlled-pcrgate.hidden"
    moved_agent_bin = False
    if BIN.exists():
        shutil.move(str(BIN), str(hidden_agent_bin))
        moved_agent_bin = True
    try:
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
        proc = run_candidate_binary(
            [str(verifier_bin), "--policy", str(policy_path), "--events", str(events_dir), "--out", str(out_path)],
            tmp_path,
            env,
        )
    finally:
        if moved_agent_bin:
            shutil.move(str(hidden_agent_bin), str(BIN))
    assert out_path.exists(), proc.stderr
    with out_path.open(encoding="utf-8") as f:
        return proc, json.load(f)


def test_admits_coherent_measured_boot_evidence(tmp_path):
    """The gate accepts a local measured-boot bundle whose PCRs and policy sequence agree."""
    events = [
        (0, 4, "separator", (1, 2)),
        (0, 0x800000E0, "SecureBoot|db|v=3|CN=Root", (1, 2)),
        (0, 5, "ExitBootServices", (1, 2)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha1", "sha256"],
        "expected": {"sha1:0": pcrs["sha1"]["0"], "sha256:0": pcrs["sha256"]["0"]},
        "min_authority_version": 2,
        "reject_replay": True,
    }
    proc, report = run_gate(tmp_path, {"001.evt": events}, policy)
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["errors"] == []
    assert report["events"] == 3
    assert report["pcrs"]["sha1"]["0"] == pcrs["sha1"]["0"]
    assert report["pcrs"]["sha256"]["0"] == pcrs["sha256"]["0"]


def test_preserves_bank_state_across_partial_evidence(tmp_path):
    """The gate scores complete evidence before a damaged suffix and emits the required warning token."""
    events = [
        (7, 4, "separator", (2,)),
        (7, 0x800000E0, "secureboot|DB|v=9|CN=CA", (1, 2)),
        (7, 5, "ExitBootServices", (2,)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha1", "sha256"],
        "expected": {"sha1:7": pcrs["sha1"]["7"], "sha256:7": pcrs["sha256"]["7"]},
        "min_authority_version": 4,
        "reject_replay": True,
    }
    proc, report = run_gate(tmp_path, {"b.evt": events + [b"\x00\x01\x02\x03\x04"]}, policy)
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["events"] == 3
    assert "truncat" in report["warnings"]
    assert report["pcrs"]["sha1"]["7"] == pcrs["sha1"]["7"]
    assert report["pcrs"]["sha256"]["7"] == pcrs["sha256"]["7"]


def test_uses_recorded_digest_bytes_for_pcr_extension(tmp_path):
    """The gate extends PCRs from the digest bytes in the record, not from the payload text."""
    digest = bytes(range(32))
    expected = extend(b"\x00" * 32, digest, 2).hex()
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:9": expected},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    proc, report = run_gate(
        tmp_path,
        {"raw-digest.evt": [record_with_digest_specs(9, 4, "separator", [(2, digest)])]},
        policy,
    )
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["events"] == 1
    assert report["pcrs"]["sha256"]["9"] == expected


def test_expected_bank_key_does_not_make_bank_required(tmp_path):
    """The gate ignores wrong-length digests and treats required_banks separately from expected keys."""
    payload = "separator"
    sha1_digest = h(1, payload.encode())
    sha1_expected = extend(b"\x00" * 20, sha1_digest, 1).hex()
    zero_sha256 = (b"\x00" * 32).hex()
    policy = {
        "required_banks": [],
        "expected": {"sha1:6": sha1_expected, "sha256:6": zero_sha256},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    proc, report = run_gate(
        tmp_path,
        {"mixed-digests.evt": [record_with_digest_specs(6, 4, payload, [(2, b"x" * 31), (1, sha1_digest)])]},
        policy,
    )
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["errors"] == []
    assert report["events"] == 1
    assert report["pcrs"]["sha1"]["6"] == sha1_expected
    assert report["pcrs"]["sha256"]["6"] == zero_sha256


def test_expected_policy_pcr_is_reported_at_zero_without_matching_evidence(tmp_path):
    """The report includes zero-state PCR entries named only by expected policy keys."""
    events = [(5, 4, "separator", (1,))]
    pcrs = expected_pcr(events)
    zero_sha256 = (b"\x00" * 32).hex()
    policy = {
        "required_banks": [],
        "expected": {"sha1:5": pcrs["sha1"]["5"], "sha256:10": zero_sha256},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"expected-zero.evt": events}, policy)
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["pcrs"]["sha1"]["5"] == pcrs["sha1"]["5"]
    assert report["pcrs"]["sha256"]["10"] == zero_sha256


def test_wrong_length_digest_does_not_occupy_duplicate_slot(tmp_path):
    """Wrong-length bank digests are skipped before first-valid and duplicate-valid decisions."""
    bad = b"x" * 31
    first = bytes(range(32))
    second = bytes(range(32, 64))
    expected = extend(b"\x00" * 32, first, 2).hex()
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:12": expected},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    proc, report = run_gate(
        tmp_path,
        {"wrong-length-then-valid.evt": [record_with_digest_specs(12, 4, "separator", [(2, bad), (2, first), (2, second)])]},
        policy,
    )
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["pcrs"]["sha256"]["12"] == expected
    assert sum("duplicate" in w.lower() for w in report["warnings"]) == 1


def test_denies_pcr_mismatch(tmp_path):
    """The gate denies pcr-mismatch evidence when the computed PCR differs from policy."""
    events = [
        (0, 4, "separator", (1, 2)),
        (0, 0x800000E0, "SecureBoot|db|v=3|CN=Root", (1, 2)),
    ]
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:0": "ab" * 32},
        "min_authority_version": 1,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"mismatch.evt": events}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert report["events"] == 2
    assert "pcr-mismatch" in report["errors"]


def test_denies_missing_required_bank(tmp_path):
    """The gate denies required-bank evidence when a required digest bank never appears."""
    events = [(0, 4, "separator", (1,))]
    policy = {
        "required_banks": ["sha256"],
        "expected": {},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"missing-bank.evt": events}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert report["events"] == 1
    assert "required-bank" in report["errors"]


def test_denies_below_minimum_authority(tmp_path):
    """The gate denies authority-version evidence when authority is below policy minimum."""
    events = [
        (0, 4, "separator", (1, 2)),
        (0, 0x800000E0, "SecureBoot|db|v=2|CN=Root", (1, 2)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:0": pcrs["sha256"]["0"]},
        "min_authority_version": 5,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"authority-version.evt": events}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert report["events"] == 2
    assert "authority-version" in report["errors"]


def test_denies_replay_and_authority_downgrade(tmp_path):
    """The gate denies authority-downgrade and replay evidence even when PCRs match."""
    events = [
        (2, 4, "separator", (1, 2)),
        (2, 0x800000E0, "SecureBoot|db|v=8|CN=A", (1, 2)),
        (2, 0x800000E0, "secureboot|DB|v=6|CN=A", (1, 2)),
        (2, 0x800000E0, "secureboot|db|v=6|CN=A", (1, 2)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha1", "sha256"],
        "expected": {"sha1:2": pcrs["sha1"]["2"], "sha256:2": pcrs["sha256"]["2"]},
        "min_authority_version": 5,
        "reject_replay": True,
    }
    proc, report = run_gate(tmp_path, {"a.evt": [events[0]], "b.evt": events[1:]}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert report["events"] == 4
    assert "authority-downgrade" in report["errors"]
    assert "replay" in report["errors"]
    assert report["pcrs"]["sha256"]["2"] == pcrs["sha256"]["2"]


def test_denies_out_of_order_authority_even_with_matching_pcr(tmp_path):
    """The gate denies ordering evidence before separator independently of PCR comparison."""
    events = [
        (4, 0x800000E0, "SecureBoot|db|v=5|CN=Late", (1, 2)),
        (4, 4, "separator", (1, 2)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha1", "sha256"],
        "expected": {"sha1:4": pcrs["sha1"]["4"], "sha256:4": pcrs["sha256"]["4"]},
        "min_authority_version": 1,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"evidence.evt": events}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert report["events"] == 2
    assert "ordering" in report["errors"]


def test_denies_authority_after_exit_even_with_matching_pcr(tmp_path):
    """The gate denies ordering evidence after the boot-services exit action."""
    events = [
        (4, 4, "separator", (1, 2)),
        (4, 5, "ExitBootServices", (1, 2)),
        (4, 0x800000E0, "SecureBoot|db|v=3|CN=Late", (1, 2)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha1", "sha256"],
        "expected": {"sha1:4": pcrs["sha1"]["4"], "sha256:4": pcrs["sha256"]["4"]},
        "min_authority_version": 1,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"post-exit.evt": events}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert report["events"] == 3
    assert "ordering" in report["errors"]


def test_denies_authority_after_case_insensitive_exit_marker(tmp_path):
    """The gate treats ExitBootServices payload matching as case-insensitive for ordering."""
    events = [
        (3, 4, "separator", (1, 2)),
        (3, 5, "eXiTbOoTsErViCeS", (1, 2)),
        (3, 0x800000E0, "SecureBoot|db|v=7|CN=Late", (1, 2)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha1", "sha256"],
        "expected": {"sha1:3": pcrs["sha1"]["3"], "sha256:3": pcrs["sha256"]["3"]},
        "min_authority_version": 1,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"case-exit.evt": events}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert report["events"] == 3
    assert "ordering" in report["errors"]


def test_duplicate_digest_uses_first_valid_bank_digest(tmp_path):
    """The gate uses the first valid digest per bank in a record and warns about duplicates."""
    first = bytes(range(32))
    second = bytes(range(32, 64))
    expected = extend(b"\x00" * 32, first, 2).hex()
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:11": expected},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    proc, report = run_gate(
        tmp_path,
        {"duplicates.evt": [record_with_digest_specs(11, 4, "separator", [(2, first), (2, second)])]},
        policy,
    )
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["events"] == 1
    assert report["pcrs"]["sha256"]["11"] == expected
    assert any("duplicate" in w.lower() for w in report["warnings"]), report


def test_authority_replay_canonicalizes_spacing_and_spaced_version(tmp_path):
    """The gate normalizes authority spacing for replay and parses spaced version fields."""
    events = [
        (8, 4, "separator", (1, 2)),
        (8, 0x800000E0, "SecureBoot | db | V = 8 | CN=A", (1, 2)),
        (8, 0x800000E0, "secureboot|DB|v=8|cn=a", (1, 2)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha1", "sha256"],
        "expected": {"sha1:8": pcrs["sha1"]["8"], "sha256:8": pcrs["sha256"]["8"]},
        "min_authority_version": 8,
        "reject_replay": True,
    }
    proc, report = run_gate(tmp_path, {"canonical-auth.evt": events}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert report["events"] == 3
    assert "replay" in report["errors"]
    assert "authority-version" not in report["errors"]
    assert "authority-downgrade" not in report["errors"]


def test_authority_replay_keeps_inner_payload_spacing_significant(tmp_path):
    """Replay canonicalization removes separator-adjacent spacing without deleting field contents."""
    events = [
        (9, 4, "separator", (1, 2)),
        (9, 0x800000E0, "SecureBoot|db|v=8|CN=A B", (1, 2)),
        (9, 0x800000E0, "secureboot | DB | v = 8 | cn=AB", (1, 2)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha1", "sha256"],
        "expected": {"sha1:9": pcrs["sha1"]["9"], "sha256:9": pcrs["sha256"]["9"]},
        "min_authority_version": 8,
        "reject_replay": True,
    }
    proc, report = run_gate(tmp_path, {"canonical-inner-space.evt": events}, policy)
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["errors"] == []
    assert report["events"] == 3


@pytest.mark.parametrize("cut", [43, 77, 78, -1])
def test_truncated_record_has_no_partial_side_effects(tmp_path, cut):
    """A partial record cannot extend a PCR, establish a bank, or emit duplicate warnings."""
    first = bytes(range(32))
    second = bytes(range(32, 64))
    incomplete = record_with_digest_specs(
        13,
        0x800000E0,
        "SecureBoot|db|v=9|CN=Partial",
        [(2, first), (2, second)],
    )
    fragment = incomplete[:cut]
    zero = (b"\x00" * 32).hex()
    policy = {
        "required_banks": ["sha1"],
        "expected": {"sha256:13": zero},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    proc, report = run_gate(
        tmp_path,
        {"atomic.evt": [(1, 4, "separator", (1,)), fragment]},
        policy,
    )
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["events"] == 1
    assert report["pcrs"]["sha256"]["13"] == zero
    assert "truncat" in report["warnings"]
    assert not any("duplicate" in warning.lower() for warning in report["warnings"])


def test_required_bank_ignores_digest_from_truncated_record(tmp_path):
    """A valid-length digest inside an incomplete record does not satisfy required-bank."""
    incomplete = record_with_digest_specs(
        6,
        4,
        "unfinished",
        [(2, bytes(range(32)))],
    )[:-1]
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:6": (b"\x00" * 32).hex()},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"incomplete-bank.evt": [incomplete]}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert report["events"] == 0
    assert "required-bank" in report["errors"]
    assert "pcr-mismatch" not in report["errors"]


def test_recovers_in_later_file_after_truncated_record(tmp_path):
    """A damaged suffix ends only its file and later basenames still contribute evidence."""
    partial = record_with_digest_specs(
        3,
        0x800000E0,
        "SecureBoot|db|v=20|CN=Discarded",
        [(2, bytes(range(32)))],
    )[:-3]
    complete = [
        (3, 4, "separator", (2,)),
        (3, 0x800000E0, "SecureBoot|db|v=4|CN=Accepted", (2,)),
    ]
    pcrs = expected_pcr(complete)
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:3": pcrs["sha256"]["3"]},
        "min_authority_version": 4,
        "reject_replay": False,
    }
    proc, report = run_gate(
        tmp_path,
        {"20-finish.evt": [complete[1]], "10-start.evt": [complete[0], partial]},
        policy,
    )
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["events"] == 2
    assert report["pcrs"]["sha256"]["3"] == pcrs["sha256"]["3"]


def test_basename_order_preserves_cross_file_authority_streams(tmp_path):
    """Creation order cannot change basename-ordered authority stream evaluation."""
    first = [(10, 4, "separator", (2,)), (10, 0x800000E0, "SecureBoot|dbx|v=3|CN=Aux", (2,))]
    second = [(10, 0x800000E0, "SecureBoot | db | v = 9 | CN=Root", (2,))]
    events = first + second
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:10": pcrs["sha256"]["10"]},
        "min_authority_version": 1,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"20-db.evt": second, "10-dbx.evt": first}, policy)
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["errors"] == []
    assert report["events"] == 3
    assert report["pcrs"]["sha256"]["10"] == pcrs["sha256"]["10"]


def test_authority_versions_are_independent_between_streams(tmp_path):
    """Interleaved authority streams maintain independent nondecreasing versions."""
    events = [
        (2, 4, "separator", (1, 2)),
        (2, 0x800000E0, "SecureBoot|db|v=8|CN=A", (1, 2)),
        (2, 0x800000E0, "SecureBoot | dbx | v = 3 | CN=B", (1, 2)),
        (2, 0x800000E0, "secureboot|DB|v=9|CN=C", (1, 2)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha1", "sha256"],
        "expected": {"sha1:2": pcrs["sha1"]["2"], "sha256:2": pcrs["sha256"]["2"]},
        "min_authority_version": 3,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"streams.evt": events}, policy)
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert "authority-downgrade" not in report["errors"]


def test_detects_downgrade_in_canonical_authority_stream(tmp_path):
    """Case and separator spacing do not split one authority stream's version history."""
    events = [
        (5, 4, "separator", (1, 2)),
        (5, 0x800000E0, "SecureBoot | DB | v = 12 | CN=A", (1, 2)),
        (5, 0x800000E0, "secureboot|db|V=11|CN=B", (1, 2)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:5": pcrs["sha256"]["5"]},
        "min_authority_version": 1,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"same-stream.evt": events}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert "authority-downgrade" in report["errors"]


def test_version_parser_requires_exact_field_key(tmp_path):
    """Text resembling a version assignment inside another key is not the authority version."""
    events = [
        (7, 4, "separator", (2,)),
        (7, 0x800000E0, "SecureBoot|db|rev = 99|v = 6|CN=A", (2,)),
    ]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:7": pcrs["sha256"]["7"]},
        "min_authority_version": 50,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"exact-version.evt": events}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert "authority-version" in report["errors"]


def test_empty_files_and_zero_length_payloads_are_valid_evidence(tmp_path):
    """Empty event-log files are inert and zero-length payload records still extend PCRs."""
    events = [(15, 4, "", (2,))]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:15": pcrs["sha256"]["15"]},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"000-empty.evt": [], "010-zero.evt": events}, policy)
    assert proc.returncode == 0, report
    assert report["accepted"] is True
    assert report["errors"] == []
    assert report["events"] == 1
    assert report["pcrs"]["sha256"]["15"] == pcrs["sha256"]["15"]


def test_diagnostics_are_deduplicated_in_first_observed_order(tmp_path):
    """Repeated warning and denial conditions emit one stable token per diagnostic category."""
    first = bytes(range(32))
    second = bytes(range(32, 64))
    duplicate_event = record_with_digest_specs(16, 4, "separator", [(2, first), (2, second)])
    events = [duplicate_event, duplicate_event]
    policy = {
        "required_banks": ["sha256"],
        "expected": {"sha256:16": "ab" * 32, "sha256:17": "cd" * 32},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    proc, report = run_gate(tmp_path, {"dups.evt": events}, policy)
    assert proc.returncode != 0
    assert report["accepted"] is False
    assert report["warnings"].count("duplicate-digest") == 1
    assert report["errors"].count("pcr-mismatch") == 1
    assert report["warnings"].index("duplicate-digest") == 0


def test_report_is_repeatable_and_has_the_required_schema(tmp_path):
    """Identical evidence produces identical JSON with every required top-level report field."""
    events = [(14, 4, "separator", (1, 2))]
    pcrs = expected_pcr(events)
    policy = {
        "required_banks": ["sha1", "sha256"],
        "expected": {"sha1:14": pcrs["sha1"]["14"], "sha256:14": pcrs["sha256"]["14"]},
        "min_authority_version": 0,
        "reject_replay": False,
    }
    outputs = []
    for name in ("first", "second"):
        work = tmp_path / name
        work.mkdir()
        proc, report = run_gate(work, {"stable.evt": events}, policy)
        assert proc.returncode == 0, report
        assert set(report) == {"accepted", "errors", "warnings", "events", "pcrs"}
        outputs.append((work / "report.json").read_bytes())
    assert outputs[0] == outputs[1]
