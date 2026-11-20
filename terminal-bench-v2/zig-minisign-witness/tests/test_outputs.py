import copy
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path("/app/witnessgate")
FIXTURES = APP / "fixtures"
BIN = APP / "bin" / "witnessgate"
REASON_ORDER = [
    "malformed",
    "bad_release_signature",
    "bad_checkpoint_signature",
    "insufficient_witness_quorum",
    "history_mismatch",
    "split_view",
    "replay",
]


@pytest.fixture(scope="session", autouse=True)
def build_witnessgate():
    """Build the native Zig admission binary once for the verifier session."""
    subprocess.run([str(APP / "scripts" / "build.sh")], cwd=APP, check=True)
    assert BIN.exists() and os.access(BIN, os.X_OK)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def release_transcript(release: dict) -> str:
    return (
        "minisign-release-v1\n"
        f"artifact_sha256={release['artifact_sha256']}\n"
        f"project={release['project']}\n"
        f"sequence={release['sequence']}\n"
        f"version={release['version']}\n"
    )


def sig(key: str, transcript: str) -> str:
    return sha256_text(f"local-sig-v1\n{key}\n{transcript}")


def release_leaf(release: dict) -> str:
    return sha256_text("leaf-v1\n" + release_transcript(release))


def checkpoint_transcript(log: dict) -> str:
    return (
        "checkpoint-v1\n"
        f"log_id={log['log_id']}\n"
        f"prev_root_hash={log['prev_root_hash']}\n"
        f"prev_size={log['prev_size']}\n"
        f"root_hash={log['root_hash']}\n"
        f"tree_size={log['tree_size']}\n"
    )


def witness_transcript(log: dict, witness_id: str) -> str:
    return (
        "witness-v1\n"
        f"log_id={log['log_id']}\n"
        f"root_hash={log['root_hash']}\n"
        f"tree_size={log['tree_size']}\n"
        f"witness={witness_id}\n"
    )


def fold_root(prev_root: str, entries: list[dict]) -> str:
    root = prev_root
    for entry in sorted(entries, key=lambda item: item["index"]):
        root = sha256_text(f"node-v1\n{root}\n{entry['leaf_hash']}\n")
    return root


def bundle_id(release: dict, log: dict) -> str:
    return sha256_text(
        f"bundle-v1\n{release_leaf(release)}\n{log['log_id']}\n{log['tree_size']}\n{log['root_hash']}\n"
    )


def load_public_case(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    trust = case_dir / "trust.json"
    bundle = case_dir / "bundle.json"
    state = case_dir / "state.json"
    shutil.copy(FIXTURES / "trust.json", trust)
    shutil.copy(FIXTURES / "bundle.json", bundle)
    shutil.copy(FIXTURES / "state.json", state)
    return trust, bundle, state, case_dir / "report.json"


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def invoke(
    trust: Path, bundle: Path, state: Path, report: Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            str(BIN),
            "--trust",
            str(trust),
            "--bundle",
            str(bundle),
            "--state",
            str(state),
            "--out",
            str(report),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def file_hashes(paths: list[Path]) -> dict[Path, str]:
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def expected_report(trust: dict, bundle: dict, state: dict, reasons: list[str]) -> dict:
    release = bundle.get("release", {})
    log = bundle.get("log", {})
    valid_ids: list[str] = []
    seen = set()
    for witness in bundle.get("witnesses", []):
        wid = witness.get("id")
        key = trust.get("witness_keys", {}).get(wid)
        if not isinstance(wid, str) or not isinstance(key, str) or wid in seen:
            continue
        seen.add(wid)
        if (
            witness.get("tree_size") == log.get("tree_size")
            and witness.get("root_hash") == log.get("root_hash")
            and witness.get("sig") == sig(key, witness_transcript(log, wid))
        ):
            valid_ids.append(wid)
    valid_ids.sort()
    ordered_reasons = [reason for reason in REASON_ORDER if reason in reasons]
    accepted = not ordered_reasons
    return {
        "accepted": accepted,
        "status": "accepted" if accepted else "rejected",
        "bundle_id": bundle_id(release, log),
        "release": {
            "project": release["project"],
            "version": release["version"],
            "sequence": release["sequence"],
            "artifact_sha256": release["artifact_sha256"],
        },
        "log": {
            "log_id": log["log_id"],
            "previous_size": log["prev_size"],
            "tree_size": log["tree_size"],
            "root_hash": log["root_hash"],
        },
        "witness_quorum": {
            "required": trust["quorum"],
            "valid": len(valid_ids),
            "accepted_ids": valid_ids,
        },
        "reasons": ordered_reasons,
    }


def assert_report_order_and_indentation(raw: str) -> None:
    """Raw reports must keep the public top-level key order and two-space JSON layout."""
    pairs = json.loads(raw, object_pairs_hook=list)
    assert [key for key, _value in pairs] == [
        "accepted",
        "status",
        "bundle_id",
        "release",
        "log",
        "witness_quorum",
        "reasons",
    ]
    by_key = dict(pairs)
    assert [key for key, _value in by_key["release"]] == [
        "project",
        "version",
        "sequence",
        "artifact_sha256",
    ]
    assert [key for key, _value in by_key["log"]] == [
        "log_id",
        "previous_size",
        "tree_size",
        "root_hash",
    ]
    assert [key for key, _value in by_key["witness_quorum"]] == [
        "required",
        "valid",
        "accepted_ids",
    ]
    assert raw.endswith("\n")
    assert '\n  "accepted"' in raw
    assert '\n    "project"' in raw
    assert "\t" not in raw


def assert_default_rejection_report(report: dict) -> None:
    """Malformed inputs that cannot yield report fields must use documented placeholders."""
    assert report == {
        "accepted": False,
        "status": "rejected",
        "bundle_id": "",
        "release": {"project": "", "version": "", "sequence": 0, "artifact_sha256": ""},
        "log": {"log_id": "", "previous_size": 0, "tree_size": 0, "root_hash": ""},
        "witness_quorum": {"required": 0, "valid": 0, "accepted_ids": []},
        "reasons": ["malformed"],
    }


def make_case(
    tmp_path: Path, project="crate", version="1.0.0", sequence=10, prev_size=3
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    trust = {
        "release_keys": {"rel": "rel-key"},
        "log_keys": {"main": "log-key"},
        "witness_keys": {
            "beta": "wit-beta",
            "alpha": "wit-alpha",
            "gamma": "wit-gamma",
        },
        "quorum": 2,
    }
    state = {
        "logs": {"main": {"tree_size": prev_size, "root_hash": "b" * 64}},
        "seen_releases": [
            {
                "project": project,
                "version": "0.9.0",
                "sequence": sequence - 1,
                "artifact_sha256": "c" * 64,
            }
        ],
    }
    release = {
        "project": project,
        "version": version,
        "artifact_sha256": hashlib.sha256(f"{project}-{version}".encode()).hexdigest(),
        "sequence": sequence,
        "signer": "rel",
    }
    release["statement_sig"] = sig("rel-key", release_transcript(release))
    leaves = [sha256_text(f"generated leaf {idx}") for idx in range(2)] + [
        release_leaf(release)
    ]
    entries = [
        {"index": prev_size + i, "leaf_hash": leaf} for i, leaf in enumerate(leaves)
    ]
    log = {
        "log_id": "main",
        "prev_size": prev_size,
        "prev_root_hash": "b" * 64,
        "tree_size": prev_size + len(entries),
        "root_hash": fold_root("b" * 64, entries),
    }
    log["checkpoint_sig"] = sig("log-key", checkpoint_transcript(log))
    witnesses = []
    for wid, key in trust["witness_keys"].items():
        witnesses.append(
            {
                "id": wid,
                "tree_size": log["tree_size"],
                "root_hash": log["root_hash"],
                "sig": sig(key, witness_transcript(log, wid)),
            }
        )
    bundle = {
        "release": release,
        "log": log,
        "entries": list(reversed(entries)),
        "witnesses": witnesses,
    }
    paths = (tmp_path / "trust.json", tmp_path / "bundle.json", tmp_path / "state.json")
    for path, obj in zip(paths, (trust, bundle, state)):
        write_json(path, obj)
    return trust, bundle, state, paths[0], paths[1], paths[2], tmp_path / "report.json"


def test_public_fixture_is_admitted_with_deterministic_report_and_no_input_mutation(
    tmp_path: Path,
):
    """The bundled admissible dossier must produce the exact public report without changing evidence."""
    trust_path, bundle_path, state_path, report_path = load_public_case(tmp_path)
    hashes = file_hashes([trust_path, bundle_path, state_path])
    trust = json.loads(trust_path.read_text())
    bundle = json.loads(bundle_path.read_text())
    state = json.loads(state_path.read_text())

    first = invoke(trust_path, bundle_path, state_path, report_path)
    assert first.returncode == 0, first.stderr
    assert file_hashes([trust_path, bundle_path, state_path]) == hashes
    report = json.loads(report_path.read_text())
    assert report == expected_report(trust, bundle, state, [])
    assert_report_order_and_indentation(report_path.read_text())

    first_bytes = report_path.read_bytes()
    second = invoke(trust_path, bundle_path, state_path, report_path)
    assert second.returncode == 0, second.stderr
    assert report_path.read_bytes() == first_bytes


def test_generated_extension_uses_canonical_transcripts_quorum_sorting_and_index_order(
    tmp_path: Path,
):
    """A larger same-schema case checks transcript signatures, quorum ids, and append-only folding order."""
    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path
    )

    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text())
    assert report == expected_report(trust, bundle, state, [])
    assert report["witness_quorum"]["accepted_ids"] == ["alpha", "beta", "gamma"]
    assert report["log"]["root_hash"] == fold_root(
        state["logs"]["main"]["root_hash"], bundle["entries"]
    )


def test_checkpoint_previous_root_must_match_stored_log_state(tmp_path: Path):
    """A validly signed checkpoint with the wrong prior root must reject as an append-only history mismatch."""
    trust, bundle, state, trust_path, bundle_path, _state_path, report_path = make_case(
        tmp_path
    )
    bundle["log"]["prev_root_hash"] = "a" * 64
    bundle["log"]["checkpoint_sig"] = sig(
        "log-key", checkpoint_transcript(bundle["log"])
    )
    write_json(bundle_path, bundle)

    result = invoke(trust_path, bundle_path, _state_path, report_path)
    assert result.returncode != 0
    assert json.loads(report_path.read_text()) == expected_report(
        trust, bundle, state, ["history_mismatch"]
    )


def test_replay_and_split_view_rejections_keep_reports_semantic(tmp_path: Path):
    """State-dependent replay and same-size split-view decisions must reject without omitting reports."""
    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "replay", project="daemon", sequence=8
    )
    state["seen_releases"].append(
        {
            "project": "daemon",
            "version": bundle["release"]["version"],
            "sequence": 8,
            "artifact_sha256": "d" * 64,
        }
    )
    write_json(state_path, state)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    assert json.loads(report_path.read_text()) == expected_report(
        trust, bundle, state, ["replay"]
    )

    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "split", project="daemon", sequence=12, prev_size=6
    )
    state["logs"]["main"] = {
        "tree_size": bundle["log"]["tree_size"],
        "root_hash": "0" * 64,
    }
    state["seen_releases"].append(
        {
            "project": "daemon",
            "version": "older-name",
            "sequence": 12,
            "artifact_sha256": "9" * 64,
        }
    )
    bundle["log"]["prev_size"] = state["logs"]["main"]["tree_size"]
    bundle["log"]["prev_root_hash"] = state["logs"]["main"]["root_hash"]
    bundle["entries"] = []
    bundle["log"]["checkpoint_sig"] = sig(
        "log-key", checkpoint_transcript(bundle["log"])
    )
    for witness in bundle["witnesses"]:
        witness["sig"] = sig(
            trust["witness_keys"][witness["id"]],
            witness_transcript(bundle["log"], witness["id"]),
        )
    write_json(state_path, state)
    write_json(bundle_path, bundle)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert report["reasons"] == ["history_mismatch", "split_view", "replay"]
    assert report["accepted"] is False and report["status"] == "rejected"


def test_signature_quorum_and_checkpoint_failures_are_independent(tmp_path: Path):
    """Forgery classes are reported independently and duplicate witnesses do not satisfy quorum."""
    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path
    )
    bundle["release"]["statement_sig"] = "0" * 64
    bundle["log"]["checkpoint_sig"] = "1" * 64
    bundle["witnesses"] = [
        copy.deepcopy(bundle["witnesses"][0]),
        copy.deepcopy(bundle["witnesses"][0]),
    ]
    write_json(bundle_path, bundle)

    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert report["reasons"] == [
        "malformed",
        "bad_release_signature",
        "bad_checkpoint_signature",
        "insufficient_witness_quorum",
    ]
    assert report["witness_quorum"]["valid"] == 1
    assert report["witness_quorum"]["accepted_ids"] == [bundle["witnesses"][0]["id"]]


def test_history_extension_rejects_missing_duplicate_or_foreign_entries(tmp_path: Path):
    """The append-only extension must cover exactly the next indices and include the release leaf."""
    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path
    )
    duplicate = copy.deepcopy(bundle)
    duplicate["entries"][1]["index"] = duplicate["entries"][0]["index"]
    write_json(bundle_path, duplicate)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert "malformed" in report["reasons"]
    assert "history_mismatch" in report["reasons"]

    missing_leaf = copy.deepcopy(bundle)
    actual_leaf = release_leaf(missing_leaf["release"])
    for entry in missing_leaf["entries"]:
        if entry["leaf_hash"] == actual_leaf:
            entry["leaf_hash"] = sha256_text("not the release")
            break
    missing_leaf["log"]["root_hash"] = fold_root(
        missing_leaf["log"]["prev_root_hash"], missing_leaf["entries"]
    )
    missing_leaf["log"]["checkpoint_sig"] = sig(
        "log-key", checkpoint_transcript(missing_leaf["log"])
    )
    for witness in missing_leaf["witnesses"]:
        witness["root_hash"] = missing_leaf["log"]["root_hash"]
        witness["sig"] = sig(
            trust["witness_keys"][witness["id"]],
            witness_transcript(missing_leaf["log"], witness["id"]),
        )
    write_json(bundle_path, missing_leaf)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    assert json.loads(report_path.read_text())["reasons"] == ["history_mismatch"]


def test_malformed_input_still_writes_rejection_report(tmp_path: Path):
    """Wrong JSON types and non-lowercase digests must reject as malformed while preserving output schema."""
    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path
    )
    bundle["release"]["artifact_sha256"] = "A" * 64
    bundle["log"]["tree_size"] = "not-an-integer"
    write_json(bundle_path, bundle)

    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert report["accepted"] is False
    assert report["status"] == "rejected"
    assert report["reasons"] == ["malformed"]
    assert set(report) == {
        "accepted",
        "status",
        "bundle_id",
        "release",
        "log",
        "witness_quorum",
        "reasons",
    }


def test_malformed_json_missing_fields_and_negative_numbers_use_rejection_defaults(
    tmp_path: Path,
):
    """Parse errors, absent report fields, and negative integers must reject with documented placeholders."""
    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "parse"
    )
    bundle_path.write_text('{"release": ', encoding="utf-8")
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    assert_default_rejection_report(json.loads(report_path.read_text()))
    assert_report_order_and_indentation(report_path.read_text())

    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "missing"
    )
    del bundle["release"]["project"]
    del bundle["log"]["root_hash"]
    write_json(bundle_path, bundle)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert report["reasons"] == ["malformed"]
    assert report["release"]["project"] == ""
    assert report["log"]["root_hash"] == ""
    assert report["bundle_id"] == ""

    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "negative"
    )
    bundle["release"]["sequence"] = -7
    bundle["log"]["tree_size"] = -1
    write_json(bundle_path, bundle)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert report["reasons"] == ["malformed"]
    assert report["release"]["sequence"] == 0
    assert report["log"]["tree_size"] == 0


def test_untrusted_verification_ids_reject_as_malformed(tmp_path: Path):
    """Unknown release, log, and witness identifiers needed for verification must reject the bundle."""
    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "release-id"
    )
    bundle["release"]["signer"] = "intruder"
    write_json(bundle_path, bundle)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert report["reasons"] == ["malformed", "bad_release_signature"]

    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "log-id"
    )
    bundle["log"]["log_id"] = "shadow"
    state["logs"]["shadow"] = {
        "tree_size": bundle["log"]["prev_size"],
        "root_hash": bundle["log"]["prev_root_hash"],
    }
    for witness in bundle["witnesses"]:
        witness["sig"] = sig(
            trust["witness_keys"][witness["id"]],
            witness_transcript(bundle["log"], witness["id"]),
        )
    write_json(bundle_path, bundle)
    write_json(state_path, state)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert "malformed" in report["reasons"]
    assert "bad_checkpoint_signature" in report["reasons"]

    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "witness-id"
    )
    bundle["witnesses"][0]["id"] = "omega"
    bundle["witnesses"][0]["sig"] = sig(
        "wit-omega", witness_transcript(bundle["log"], "omega")
    )
    write_json(bundle_path, bundle)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert report["reasons"] == ["malformed"]


def test_history_bounds_reject_out_of_range_entries_and_shrinking_trees(tmp_path: Path):
    """Append-only history must reject entries outside the required range and shrinking checkpoints."""
    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "entry-range"
    )
    bundle["entries"][0]["index"] = bundle["log"]["prev_size"] - 1
    write_json(bundle_path, bundle)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert "malformed" in report["reasons"]
    assert "history_mismatch" in report["reasons"]

    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "shrinking"
    )
    bundle["entries"] = []
    bundle["log"]["tree_size"] = bundle["log"]["prev_size"] - 1
    bundle["log"]["root_hash"] = bundle["log"]["prev_root_hash"]
    bundle["log"]["checkpoint_sig"] = sig(
        "log-key", checkpoint_transcript(bundle["log"])
    )
    for witness in bundle["witnesses"]:
        witness["tree_size"] = bundle["log"]["tree_size"]
        witness["root_hash"] = bundle["log"]["root_hash"]
        witness["sig"] = sig(
            trust["witness_keys"][witness["id"]],
            witness_transcript(bundle["log"], witness["id"]),
        )
    write_json(bundle_path, bundle)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert report["reasons"] == ["history_mismatch"]


def test_replay_detects_same_artifact_hash_and_greater_prior_sequence(tmp_path: Path):
    """Replay state must reject same-artifact releases and older sequence submissions independently."""
    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "artifact", project="engine", sequence=20
    )
    state["seen_releases"].append(
        {
            "project": "engine",
            "version": "different-name",
            "sequence": 2,
            "artifact_sha256": bundle["release"]["artifact_sha256"],
        }
    )
    write_json(state_path, state)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    assert json.loads(report_path.read_text())["reasons"] == ["replay"]

    trust, bundle, state, trust_path, bundle_path, state_path, report_path = make_case(
        tmp_path / "sequence", project="engine", sequence=20
    )
    state["seen_releases"].append(
        {
            "project": "engine",
            "version": "future-name",
            "sequence": 21,
            "artifact_sha256": "e" * 64,
        }
    )
    write_json(state_path, state)
    result = invoke(trust_path, bundle_path, state_path, report_path)
    assert result.returncode != 0
    assert json.loads(report_path.read_text())["reasons"] == ["replay"]
