"""Verifier coverage for the Btrfs recovery auditor's replay, integrity, quota, and output contracts."""

import json
import os
import pwd
import subprocess
from pathlib import Path

import pytest

ROOT = Path("/app/btraudit")
BIN = ROOT / "bin" / "btraudit"
RUN_USER = "tbrun"
RUN_ACCOUNT = pwd.getpwnam(RUN_USER)


def _drop_to_runner():
    os.setgid(RUN_ACCOUNT.pw_gid)
    os.setuid(RUN_ACCOUNT.pw_uid)


def _make_runner_owned(path):
    os.chown(path, RUN_ACCOUNT.pw_uid, RUN_ACCOUNT.pw_gid)
    if Path(path).is_dir():
        for child in Path(path).rglob("*"):
            os.chown(child, RUN_ACCOUNT.pw_uid, RUN_ACCOUNT.pw_gid)


def _prepare_tmp_tree(tmp_path):
    for parent in tmp_path.parents:
        if parent == Path("/tmp"):
            break
        parent.chmod(0o755)
    _make_runner_owned(tmp_path)
    tmp_path.chmod(0o700)


def _seal_verifier_tree():
    tests = Path("/tests")
    for child in tests.rglob("*"):
        if child.is_dir():
            child.chmod(0o700)
        else:
            child.chmod(0o600)
    tests.chmod(0o700)


@pytest.fixture(scope="session", autouse=True)
def build_tool():
    """Build the Ada recovery auditor once for all verifier cases."""
    _seal_verifier_tree()
    _make_runner_owned(ROOT)
    result = subprocess.run(
        [str(ROOT / "scripts" / "build.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        preexec_fn=_drop_to_runner,
        check=False,
    )
    assert result.returncode == 0, f"Build failed:\n{result.stderr}\n{result.stdout}"
    assert BIN.exists(), "Binary not produced at expected path"


def run_case(tmp_path, text):
    case_dir = tmp_path / "case"
    out_dir = tmp_path / "out"
    _prepare_tmp_tree(tmp_path)
    case_dir.mkdir()
    (case_dir / "stream.rec").write_text(text)
    _make_runner_owned(tmp_path)
    result = subprocess.run(
        [str(BIN), "--case", str(case_dir), "--out", str(out_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        preexec_fn=_drop_to_runner,
        check=False,
    )
    assert result.returncode == 0, f"Run failed:\n{result.stderr}\n{result.stdout}"
    return out_dir


def read_tsv(path):
    lines = path.read_text().splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line]


def load_outputs(out_dir):
    return {
        "summary": json.loads((out_dir / "summary.json").read_text()),
        "extents": {row["extent"]: row for row in read_tsv(out_dir / "extents.tsv")},
        "quotas": {row["qgroup"]: row for row in read_tsv(out_dir / "quotas.tsv")},
        "violations": [
            line
            for line in (out_dir / "violations.txt").read_text().splitlines()
            if line
        ],
        "names": sorted(p.name for p in out_dir.iterdir()),
    }


def test_committed_boundary_and_incomplete_record_are_not_durable(tmp_path):
    """Only complete records inside committed transactions affect the recovered state."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 1;
QGROUP 1 500;
SUBVOL 10 1;
CHECK e1 aaa;
WRITE 10 /a e1 100 aaa;
COMMIT 1;
BEGIN 2;
WRITE 10 /crash e2 900 bbb
COMMIT 2;
""".lstrip(),
        )
    )
    assert out["names"] == [
        "extents.tsv",
        "quotas.tsv",
        "summary.json",
        "violations.txt",
    ]
    assert out["summary"] == {"files": 1, "extents": 1, "qgroups": 1, "violations": 0}
    assert out["extents"]["e1"] == {
        "extent": "e1",
        "len": "100",
        "hash": "aaa",
        "refs": "1",
    }
    assert out["quotas"]["1"]["referenced"] == "100"
    assert out["quotas"]["1"]["exclusive"] == "100"
    assert out["quotas"]["1"]["over"] == "false"
    assert out["violations"] == []


def test_complete_records_without_matching_commit_are_not_durable(tmp_path):
    """Complete records in an unfinished transaction are ignored at recovery time."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 1;
QGROUP 1 500;
SUBVOL 10 1;
CHECK e1 aaa;
WRITE 10 /a e1 100 aaa;
COMMIT 1;
BEGIN 2;
WRITE 10 /lost e1 100 aaa;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 1, "extents": 1, "qgroups": 1, "violations": 0}
    assert out["extents"]["e1"]["refs"] == "1"
    assert out["quotas"]["1"]["referenced"] == "100"
    assert out["quotas"]["1"]["exclusive"] == "100"
    assert out["violations"] == []


def test_mismatched_commit_identifier_discards_pending_group(tmp_path):
    """A COMMIT with the wrong id cannot make the buffered group durable."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 1;
QGROUP 1 500;
SUBVOL 1 1;
CHECK keep ok;
WRITE 1 /keep keep 80 ok;
COMMIT 1;
BEGIN 2;
WRITE 1 /lost lost 300 ok;
COMMIT 9;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 1, "extents": 1, "qgroups": 1, "violations": 0}
    assert out["extents"] == {
        "keep": {"extent": "keep", "len": "80", "hash": "ok", "refs": "1"}
    }
    assert "lost" not in out["extents"]
    assert out["quotas"]["1"]["referenced"] == "80"
    assert out["quotas"]["1"]["exclusive"] == "80"
    assert out["violations"] == []


def test_snapshot_clone_sharing_controls_exclusive_quota(tmp_path):
    """Snapshot and clone references share the same physical extent across qgroups."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 7;
QGROUP 1 1000;
QGROUP 2 1000;
SUBVOL 1 1;
CHECK e10 h10;
WRITE 1 /vm e10 300 h10;
CLONE 1 /vm /vm.copy;
SNAPSHOT 2 1 2;
DELETE 1 /vm.copy;
COMMIT 7;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 3, "extents": 1, "qgroups": 2, "violations": 0}
    assert out["extents"]["e10"]["refs"] == "3"
    assert out["quotas"]["1"]["referenced"] == "300"
    assert out["quotas"]["1"]["exclusive"] == "0"
    assert out["quotas"]["2"]["referenced"] == "600"
    assert out["quotas"]["2"]["exclusive"] == "0"
    assert out["violations"] == []


def test_same_qgroup_shared_extent_remains_exclusive(tmp_path):
    """Multiple files in one qgroup share references without losing exclusive ownership."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 8;
QGROUP 1 1000;
SUBVOL 1 1;
CHECK e30 h30;
WRITE 1 /base e30 300 h30;
CLONE 1 /base /copy;
COMMIT 8;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 2, "extents": 1, "qgroups": 1, "violations": 0}
    assert out["extents"]["e30"]["refs"] == "2"
    assert out["quotas"]["1"]["referenced"] == "600"
    assert out["quotas"]["1"]["exclusive"] == "300"
    assert out["quotas"]["1"]["over"] == "false"
    assert out["violations"] == []


def test_rename_overwrites_destination_and_keeps_new_path_live(tmp_path):
    """RENAME moves identity to the destination path and replaces its old file."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 4;
QGROUP 1 1000;
SUBVOL 1 1;
CHECK e1 h1;
CHECK e2 h2;
WRITE 1 /old e1 50 h1;
WRITE 1 /new e2 60 h2;
RENAME 1 /old /new;
CLONE 1 /new /copy;
DELETE 1 /old;
COMMIT 4;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 2, "extents": 1, "qgroups": 1, "violations": 0}
    assert out["extents"]["e1"] == {
        "extent": "e1",
        "len": "50",
        "hash": "h1",
        "refs": "2",
    }
    assert "e2" not in out["extents"]
    assert out["quotas"]["1"]["referenced"] == "100"
    assert out["quotas"]["1"]["exclusive"] == "50"
    assert out["violations"] == []


def test_subvolume_path_identity_survives_delete_and_later_clone(tmp_path):
    """Identical paths in different subvolumes remain separate during recovery."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 3;
QGROUP 1 1000;
QGROUP 2 1000;
SUBVOL 1 1;
CHECK e5 h5;
WRITE 1 /keep e5 100 h5;
SNAPSHOT 2 1 2;
DELETE 2 /keep;
CLONE 1 /keep /still;
COMMIT 3;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 2, "extents": 1, "qgroups": 2, "violations": 0}
    assert out["extents"]["e5"]["refs"] == "2"
    assert out["quotas"]["1"]["referenced"] == "200"
    assert out["quotas"]["1"]["exclusive"] == "100"
    assert out["quotas"]["2"]["referenced"] == "0"
    assert out["quotas"]["2"]["exclusive"] == "0"


def test_checksum_and_quota_violations_are_reported_deterministically(tmp_path):
    """Checksum conflicts and quota overruns appear in the sorted audit report."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 9;
QGROUP 1 150;
SUBVOL 1 1;
CHECK e1 good;
WRITE 1 /bad e1 200 wrong;
COMMIT 9;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 1, "extents": 1, "qgroups": 1, "violations": 2}
    assert out["extents"]["e1"] == {
        "extent": "e1",
        "len": "200",
        "hash": "good",
        "refs": "1",
    }
    assert out["quotas"]["1"]["referenced"] == "200"
    assert out["quotas"]["1"]["exclusive"] == "200"
    assert out["quotas"]["1"]["over"] == "true"
    assert out["violations"] == ["checksum e1", "quota-over 1"]


def test_zero_limit_qgroup_is_not_reported_over_quota(tmp_path):
    """A zero qgroup limit disables over-quota reporting while preserving accounting."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 19;
QGROUP 1 0;
SUBVOL 1 1;
CHECK e1 ok;
WRITE 1 /large e1 900 ok;
COMMIT 19;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 1, "extents": 1, "qgroups": 1, "violations": 0}
    assert out["quotas"]["1"] == {
        "qgroup": "1",
        "referenced": "900",
        "exclusive": "900",
        "limit": "0",
        "over": "false",
    }
    assert out["violations"] == []


def test_clean_case_writes_byte_empty_violations_file(tmp_path):
    """A recovered stream with no checksum or quota errors leaves violations.txt byte-empty."""
    out_dir = run_case(
        tmp_path,
        """
BEGIN 191;
QGROUP 1 1000;
SUBVOL 1 1;
CHECK clean ok;
WRITE 1 /clean clean 40 ok;
COMMIT 191;
""".lstrip(),
    )
    assert (out_dir / "violations.txt").read_bytes() == b""
    out = load_outputs(out_dir)
    assert out["summary"] == {"files": 1, "extents": 1, "qgroups": 1, "violations": 0}
    assert out["violations"] == []


def test_nested_transaction_commit_requires_outer_commit(tmp_path):
    """A committed inner group is still lost when its containing group never commits."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 10;
QGROUP 1 1000;
SUBVOL 1 1;
CHECK keep hkeep;
WRITE 1 /keep keep 90 hkeep;
COMMIT 10;
BEGIN 20;
BEGIN 21;
CHECK ghost hghost;
WRITE 1 /ghost ghost 700 hghost;
COMMIT 21;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 1, "extents": 1, "qgroups": 1, "violations": 0}
    assert out["extents"] == {
        "keep": {"extent": "keep", "len": "90", "hash": "hkeep", "refs": "1"}
    }
    assert out["quotas"]["1"]["referenced"] == "90"
    assert out["quotas"]["1"]["exclusive"] == "90"
    assert out["violations"] == []


def test_mismatched_abort_discards_all_open_groups(tmp_path):
    """An ABORT with the wrong id discards the whole open transaction stack."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
QGROUP 1 1000;
SUBVOL 1 1;
CHECK keep hkeep;
WRITE 1 /keep keep 90 hkeep;
BEGIN 210;
WRITE 1 /outer outer 500 houter;
BEGIN 211;
WRITE 1 /inner inner 600 hinner;
ABORT 999;
COMMIT 211;
COMMIT 210;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 1, "extents": 1, "qgroups": 1, "violations": 0}
    assert out["extents"] == {
        "keep": {"extent": "keep", "len": "90", "hash": "hkeep", "refs": "1"}
    }
    assert out["quotas"]["1"]["referenced"] == "90"
    assert out["violations"] == []


def test_linked_qgroup_parent_rolls_up_descendant_references(tmp_path):
    """Parent qgroups aggregate descendant references but still use extent-level exclusivity."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 30;
QGROUP 1 150;
QGROUP 2 1000;
QGROUP 9 500;
LINK_QGROUP 1 9;
LINK_QGROUP 2 9;
SUBVOL 1 1;
SUBVOL 2 2;
CHECK ea ha;
CHECK eb hb;
WRITE 1 /a ea 100 ha;
CLONE 1 /a /a.copy;
WRITE 2 /b eb 350 hb;
COMMIT 30;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 3, "extents": 2, "qgroups": 3, "violations": 2}
    assert out["extents"]["ea"] == {
        "extent": "ea",
        "len": "100",
        "hash": "ha",
        "refs": "2",
    }
    assert out["extents"]["eb"] == {
        "extent": "eb",
        "len": "350",
        "hash": "hb",
        "refs": "1",
    }
    assert out["quotas"]["1"] == {
        "qgroup": "1",
        "referenced": "200",
        "exclusive": "100",
        "limit": "150",
        "over": "true",
    }
    assert out["quotas"]["2"] == {
        "qgroup": "2",
        "referenced": "350",
        "exclusive": "350",
        "limit": "1000",
        "over": "false",
    }
    assert out["quotas"]["9"] == {
        "qgroup": "9",
        "referenced": "550",
        "exclusive": "450",
        "limit": "500",
        "over": "true",
    }
    assert out["violations"] == ["quota-over 1", "quota-over 9"]


def test_unlinked_qgroup_edge_changes_transitive_rollup(tmp_path):
    """Durable qgroup edge removal changes later ancestor accounting."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 40;
QGROUP 1 1000;
QGROUP 2 1000;
QGROUP 9 250;
QGROUP 99 1000;
LINK_QGROUP 1 9;
LINK_QGROUP 9 99;
SUBVOL 1 1;
CHECK ebase hbase;
WRITE 1 /base ebase 120 hbase;
UNLINK_QGROUP 1 9;
LINK_QGROUP 2 9;
SUBVOL 2 2;
CHECK enew hnew;
WRITE 2 /new enew 260 hnew;
COMMIT 40;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 2, "extents": 2, "qgroups": 4, "violations": 1}
    assert out["extents"]["ebase"] == {
        "extent": "ebase",
        "len": "120",
        "hash": "hbase",
        "refs": "1",
    }
    assert out["extents"]["enew"] == {
        "extent": "enew",
        "len": "260",
        "hash": "hnew",
        "refs": "1",
    }
    assert out["quotas"]["1"]["referenced"] == "120"
    assert out["quotas"]["1"]["exclusive"] == "120"
    assert out["quotas"]["9"]["referenced"] == "260"
    assert out["quotas"]["9"]["exclusive"] == "260"
    assert out["quotas"]["99"]["referenced"] == "260"
    assert out["quotas"]["99"]["exclusive"] == "260"
    assert out["violations"] == ["quota-over 9"]


def test_semicolon_stream_missing_paths_and_late_check_outputs_expected_audit(tmp_path):
    """Semicolon-delimited replay ignores missing path operations and reports late checksum conflicts."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
BEGIN 71; QGROUP 1 1000; SUBVOL 1 1; WRITE 1 /a e1 100 draft;
CLONE 1 /missing /ghost; RENAME 1 /missing /renamed; DELETE 1 /missing;
CHECK e1 final; CLONE 1 /a /b; COMMIT 71; BEGIN 72; WRITE 1 /lost e2 500 h; COMMIT 72
""".lstrip(),
        )
    )
    assert out["names"] == [
        "extents.tsv",
        "quotas.tsv",
        "summary.json",
        "violations.txt",
    ]
    assert out["summary"] == {"files": 2, "extents": 1, "qgroups": 1, "violations": 1}
    assert out["extents"] == {
        "e1": {"extent": "e1", "len": "100", "hash": "final", "refs": "2"}
    }
    assert out["quotas"] == {
        "1": {
            "qgroup": "1",
            "referenced": "200",
            "exclusive": "100",
            "limit": "1000",
            "over": "false",
        }
    }
    assert out["violations"] == ["checksum e1"]


def test_conflicting_successive_checks_are_deduplicated(tmp_path):
    """Successive durable CHECK records for one extent report a single checksum violation."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
QGROUP 1 1000;
SUBVOL 1 1;
WRITE 1 /a e1 70 first;
CHECK e1 first;
CHECK e1 second;
CHECK e1 third;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 1, "extents": 1, "qgroups": 1, "violations": 1}
    assert out["extents"] == {
        "e1": {"extent": "e1", "len": "70", "hash": "third", "refs": "1"}
    }
    assert out["violations"] == ["checksum e1"]


def test_compound_replay_preserves_recovery_and_qgroup_invariants(tmp_path):
    """A compound crash stream preserves transaction, snapshot, and qgroup invariants together."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
QGROUP 1 300; QGROUP 2 900; QGROUP 9 700; LINK_QGROUP 1 9;
BEGIN 80; SUBVOL 10 1; WRITE 10 /root e10 120 h10; CHECK e10 h10;
BEGIN 81; SNAPSHOT 20 10 2; CLONE 20 /root /copy; DELETE 10 /absent; COMMIT 81;
UNLINK_QGROUP 1 9; LINK_QGROUP 2 9; WRITE 20 /late e20 450 bad; CHECK e20 good; COMMIT 80;
BEGIN 82; WRITE 10 /crashed e30 999 h30; COMMIT 82
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 4, "extents": 2, "qgroups": 3, "violations": 1}
    assert out["extents"] == {
        "e10": {"extent": "e10", "len": "120", "hash": "h10", "refs": "3"},
        "e20": {"extent": "e20", "len": "450", "hash": "good", "refs": "1"},
    }
    assert out["quotas"] == {
        "1": {
            "qgroup": "1",
            "referenced": "120",
            "exclusive": "0",
            "limit": "300",
            "over": "false",
        },
        "2": {
            "qgroup": "2",
            "referenced": "690",
            "exclusive": "450",
            "limit": "900",
            "over": "false",
        },
        "9": {
            "qgroup": "9",
            "referenced": "690",
            "exclusive": "450",
            "limit": "700",
            "over": "false",
        },
    }
    assert out["violations"] == ["checksum e20"]


def test_abort_records_and_tab_separated_commands_do_not_leak(tmp_path):
    """Aborted transaction records and unterminated suffixes do not become durable."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
QGROUP	1	1000; SUBVOL 1 1; CHECK e1 h1; WRITE 1 /base e1 100 h1;
BEGIN 501; WRITE 1 /lost e2 900 h2; ABORT 501;
WRITE 1 /after e3 30 h3; BEGIN 502; WRITE 1 /tail e4 400 h4
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 2, "extents": 2, "qgroups": 1, "violations": 0}
    assert out["extents"] == {
        "e1": {"extent": "e1", "len": "100", "hash": "h1", "refs": "1"},
        "e3": {"extent": "e3", "len": "30", "hash": "h3", "refs": "1"},
    }
    assert out["quotas"]["1"]["referenced"] == "130"
    assert out["quotas"]["1"]["exclusive"] == "130"
    assert out["violations"] == []


def test_cross_subvolume_move_rewrite_and_scrub_recompute_quota_graph(tmp_path):
    """Cross-subvolume moves and rewrites change live ownership without reviving old extents."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
QGROUP 1 150; QGROUP 2 500; QGROUP 9 200; LINK_QGROUP 1 9; LINK_QGROUP 2 9;
SUBVOL 1 1; SUBVOL 2 2; CHECK e1 h1; CHECK e0 h0;
WRITE 1 /a e1 100 h1; WRITE 2 /dst e0 40 h0; MOVE 1 /a 2 /dst;
REWRITE 2 /dst e2 220 bad; CHECK e2 good; SCRUB e2 good;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 1, "extents": 1, "qgroups": 3, "violations": 2}
    assert out["extents"] == {
        "e2": {"extent": "e2", "len": "220", "hash": "good", "refs": "1"}
    }
    assert out["quotas"] == {
        "1": {
            "qgroup": "1",
            "referenced": "0",
            "exclusive": "0",
            "limit": "150",
            "over": "false",
        },
        "2": {
            "qgroup": "2",
            "referenced": "220",
            "exclusive": "220",
            "limit": "500",
            "over": "false",
        },
        "9": {
            "qgroup": "9",
            "referenced": "220",
            "exclusive": "220",
            "limit": "200",
            "over": "true",
        },
    }
    assert out["violations"] == ["checksum e2", "quota-over 9"]


def test_scrub_mismatch_uses_durable_check_then_write_hash(tmp_path):
    """SCRUB reports mismatches against durable check evidence or the stored write hash."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
QGROUP 1 1000;
SUBVOL 1 1;
WRITE 1 /checked e1 100 live1;
CHECK e1 canonical1;
SCRUB e1 live1;
WRITE 1 /unchecked e2 120 live2;
SCRUB e2 other2;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 2, "extents": 2, "qgroups": 1, "violations": 2}
    assert out["extents"] == {
        "e1": {"extent": "e1", "len": "100", "hash": "canonical1", "refs": "1"},
        "e2": {"extent": "e2", "len": "120", "hash": "live2", "refs": "1"},
    }
    assert out["violations"] == ["checksum e1", "checksum e2"]


def test_cyclic_qgroup_links_count_each_reachable_path_once(tmp_path):
    """Cyclic qgroup ancestry is finite and does not double-count references."""
    out = load_outputs(
        run_case(
            tmp_path,
            """
QGROUP 1 1000; QGROUP 2 1000; QGROUP 3 250; LINK_QGROUP 1 2; LINK_QGROUP 2 3; LINK_QGROUP 3 1;
SUBVOL 10 1; SUBVOL 20 3; CHECK ea ha; CHECK ec hc;
WRITE 10 /a ea 100 ha; CLONE 10 /a /b; WRITE 20 /c ec 300 hc;
""".lstrip(),
        )
    )
    assert out["summary"] == {"files": 3, "extents": 2, "qgroups": 3, "violations": 1}
    assert out["extents"]["ea"]["refs"] == "2"
    assert out["extents"]["ec"]["refs"] == "1"
    for qid in ("1", "2", "3"):
        assert out["quotas"][qid]["referenced"] == "500"
        assert out["quotas"][qid]["exclusive"] == "400"
    assert out["quotas"]["3"]["over"] == "true"
    assert out["violations"] == ["quota-over 3"]


def test_output_rows_are_canonically_sorted_and_invalid_mutations_noop(tmp_path):
    """TSV rows follow canonical ordering while invalid path mutations are ignored."""
    out_dir = run_case(
        tmp_path,
        """
QGROUP 10 1000; QGROUP 2 1000; QGROUP 1 1000; SUBVOL 1 1; SUBVOL 2 2;
WRITE 1 /c ccc 30 hc; WRITE 1 /a aaa 10 ha; WRITE 1 /b bbb 20 hb;
MOVE 1 /missing 2 /x; MOVE 1 /a 99 /lost; REWRITE 1 /missing zzz 900 hz; DELETE 2 /missing;
""".lstrip(),
    )
    out = load_outputs(out_dir)
    assert out["summary"] == {"files": 3, "extents": 3, "qgroups": 3, "violations": 0}
    assert (out_dir / "extents.tsv").read_text().splitlines() == [
        "extent\tlen\thash\trefs",
        "aaa\t10\tha\t1",
        "bbb\t20\thb\t1",
        "ccc\t30\thc\t1",
    ]
    assert [
        line.split("\t")[0]
        for line in (out_dir / "quotas.tsv").read_text().splitlines()
    ] == [
        "qgroup",
        "1",
        "2",
        "10",
    ]
    assert out["quotas"]["1"]["referenced"] == "60"
    assert out["quotas"]["2"]["referenced"] == "0"
    assert out["violations"] == []
