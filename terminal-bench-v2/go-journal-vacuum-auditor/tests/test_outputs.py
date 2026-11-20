import csv
import hashlib
import json
import subprocess
from pathlib import Path

APP = Path("/app/jvac")
BIN = APP / "bin" / "jvac"


def file_digest_tree(root: Path) -> dict[str, str]:
    digests = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digests[str(path.relative_to(root))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return digests


def parse_attrs(parts):
    attrs = {}
    for part in parts:
        if "=" not in part:
            return None
        k, v = part.split("=", 1)
        attrs[k] = v
    return attrs


def read_committed_segment(case_dir: Path, seg_path: Path):
    rel = seg_path.relative_to(case_dir).as_posix()
    data = seg_path.read_bytes()
    if b"\n" not in data:
        return None, 0, False
    header_raw, rest = data.split(b"\n", 1)
    try:
        header = header_raw.decode("utf-8").split("\t")
    except UnicodeDecodeError:
        return None, 0, False
    if len(header) != 5 or header[0] != "JSEG":
        return None, 0, False
    attrs = parse_attrs(header[1:])
    if not attrs or set(attrs) != {"name", "seq", "rotated", "bytes"}:
        return None, 0, False
    try:
        seq = int(attrs["seq"])
        size = int(attrs["bytes"])
    except ValueError:
        return None, 0, False
    if attrs["rotated"] not in {"true", "false"} or seq < 0 or size <= 0:
        return None, 0, False
    segment = {
        "path": rel,
        "seq": seq,
        "rotated": attrs["rotated"] == "true",
        "bytes": size,
        "records": [],
    }
    ignored = 0
    for index, raw in enumerate(rest.splitlines(keepends=True)):
        if not raw.endswith(b"\n"):
            ignored += 1
            break
        line = raw[:-1].decode("utf-8")
        fields = line.split("\t")
        if len(fields) != 9 or fields[0] != "R" or not fields[1] or not fields[2]:
            ignored += 1
            break
        try:
            mono = int(fields[3])
            real = int(fields[4])
            record_bytes = int(fields[8])
        except ValueError:
            ignored += 1
            break
        if record_bytes < 0:
            ignored += 1
            break
        segment["records"].append(
            {
                "cursor": fields[1],
                "boot_id": fields[2],
                "monotonic_us": mono,
                "realtime_us": real,
                "unit": fields[5],
                "priority": fields[6],
                "message": fields[7],
                "record_bytes": record_bytes,
                "record_index": index,
                "segment": rel,
                "seq": seq,
            }
        )
    return segment, ignored, True


def expected_reports(case_dir: Path):
    policy = json.loads((case_dir / "policy.json").read_text())
    segments = []
    ignored = 0
    invalid = 0
    for path in sorted((case_dir / "segments").glob("*.jseg")):
        seg, tail, ok = read_committed_segment(case_dir, path)
        ignored += tail
        if ok:
            segments.append(seg)
        else:
            invalid += 1
    records = [r for seg in segments for r in seg["records"]]
    first_by_boot = {}
    for rec in records:
        first_by_boot[rec["boot_id"]] = min(
            first_by_boot.get(rec["boot_id"], rec["realtime_us"]), rec["realtime_us"]
        )
    boots = sorted(first_by_boot, key=lambda b: (first_by_boot[b], b))
    boot_rank = {boot: i for i, boot in enumerate(boots)}

    def record_key(rec):
        return (
            boot_rank[rec["boot_id"]],
            rec["monotonic_us"],
            rec["realtime_us"],
            rec["seq"],
            rec["segment"],
            rec["record_index"],
        )

    ordered = sorted(records, key=record_key)
    record_pos = {(r["segment"], r["record_index"]): i for i, r in enumerate(ordered)}
    newest_retained = (
        set(boots[max(0, len(boots) - int(policy["retain_boots"])) :])
        if int(policy["retain_boots"])
        else set()
    )
    decisions = []
    candidates = []
    kept_total = sum(seg["bytes"] for seg in segments)
    kept_rotated = sum(seg["bytes"] for seg in segments if seg["rotated"])
    for seg in segments:
        seg_records = sorted(seg["records"], key=record_key)
        oldest = seg_records[0]["cursor"] if seg_records else ""
        newest = seg_records[-1]["cursor"] if seg_records else ""
        protected = any(
            r["boot_id"] in newest_retained
            or r["realtime_us"] >= int(policy["retain_realtime_after_us"])
            for r in seg_records
        )
        if not seg["rotated"]:
            reason = "active"
        elif protected:
            reason = "retention-window"
        else:
            reason = "budget-spare"
        decision = {
            "segment": seg,
            "action": "keep",
            "reason": reason,
            "oldest": oldest,
            "newest": newest,
        }
        decisions.append(decision)
        if seg["rotated"] and reason == "budget-spare":
            if seg_records:
                newest_pos = max(
                    record_pos[(r["segment"], r["record_index"])] for r in seg_records
                )
            else:
                newest_pos = -1
            candidates.append((newest_pos, seg["seq"], seg["path"], decision))
    for _, _, _, decision in sorted(candidates):
        if kept_total <= int(policy["max_bytes"]) and kept_rotated <= int(
            policy["max_rotated_bytes"]
        ):
            break
        decision["action"] = "delete"
        decision["reason"] = "budget"
        kept_total -= decision["segment"]["bytes"]
        kept_rotated -= decision["segment"]["bytes"]
    over_budget = kept_total > int(policy["max_bytes"]) or kept_rotated > int(
        policy["max_rotated_bytes"]
    )
    timeline = [
        {
            "rank": str(rank),
            "boot_id": r["boot_id"],
            "cursor": r["cursor"],
            "realtime_us": str(r["realtime_us"]),
            "monotonic_us": str(r["monotonic_us"]),
            "segment": r["segment"],
            "record_index": str(r["record_index"]),
            "unit": r["unit"],
            "priority": r["priority"],
            "message": r["message"],
        }
        for rank, r in enumerate(ordered)
    ]
    vacuum = [
        {
            "segment": d["segment"]["path"],
            "action": d["action"],
            "reason": d["reason"],
            "bytes": str(d["segment"]["bytes"]),
            "committed_records": str(len(d["segment"]["records"])),
            "oldest_cursor": d["oldest"],
            "newest_cursor": d["newest"],
        }
        for d in sorted(decisions, key=lambda x: x["segment"]["path"])
    ]
    summary = {
        "boots": boots,
        "committed_records": len(ordered),
        "ignored_tail_records": ignored,
        "invalid_segments": invalid,
        "over_budget": over_budget,
        "scanned_segments": len(segments) + invalid,
        "total_bytes_after_plan": kept_total,
    }
    return summary, timeline, vacuum


def run_auditor(case_dir: Path, out_dir: Path):
    subprocess.run([str(APP / "scripts" / "build.sh")], check=True, cwd=APP)
    subprocess.run(
        [str(BIN), "--case", str(case_dir), "--out", str(out_dir)], check=True, cwd=APP
    )


def read_tsv(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def assert_reports_match(case_dir: Path, out_dir: Path):
    expected_summary, expected_timeline, expected_vacuum = expected_reports(case_dir)
    assert json.loads((out_dir / "summary.json").read_text()) == expected_summary
    assert read_tsv(out_dir / "timeline.tsv") == expected_timeline
    assert read_tsv(out_dir / "vacuum.tsv") == expected_vacuum
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "summary.json",
        "timeline.tsv",
        "vacuum.tsv",
    ]


def test_site_a_reports_match_contract_and_preserve_input(tmp_path):
    """The shipped case must produce contract reports without modifying the journal evidence."""
    case_dir = APP / "cases" / "site-a"
    before = file_digest_tree(case_dir)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "stale.txt").write_text("old report\n", encoding="utf-8")
    run_auditor(case_dir, out_dir)
    assert_reports_match(case_dir, out_dir)
    assert file_digest_tree(case_dir) == before


def test_generated_case_covers_tail_invalidity_and_budget_interactions(tmp_path):
    """A fresh same-format dossier exercises ordering, invalid segments, partial tails, and both byte budgets."""
    case_dir = tmp_path / "case"
    seg_dir = case_dir / "segments"
    seg_dir.mkdir(parents=True)
    (case_dir / "policy.json").write_text(
        json.dumps(
            {
                "retain_boots": 2,
                "retain_realtime_after_us": 5000,
                "max_bytes": 920,
                "max_rotated_bytes": 450,
            }
        )
    )
    (seg_dir / "10-old.jseg").write_text(
        "JSEG\tname=old\tseq=10\trotated=true\tbytes=250\n"
        "R\to1\tboot-a\t20\t3000\tsshd\t5\told-open\t70\n"
        "R\to2\tboot-a\t25\t3010\tcron\t6\told-close\t70\n"
    )
    (seg_dir / "20-tie.jseg").write_text(
        "JSEG\tname=tie\tseq=20\trotated=true\tbytes=260\n"
        "R\tt1\tboot-b\t5\t4200\tkernel\t4\tfirst\t80\n"
        "R\tt2\tboot-b\t5\t4190\tkernel\t4\tmono-tie\t80\n"
    )
    (seg_dir / "30-late.jseg").write_text(
        "JSEG\tname=late\tseq=30\trotated=true\tbytes=300\n"
        "R\tl1\tboot-c\t1\t5000\tsystemd\t5\trotate\t90\n"
        "R\tl2\tboot-c\t2\t5100\tapp\t4\tready\t90\n"
    )
    (seg_dir / "40-active.jseg").write_text(
        "JSEG\tname=active\tseq=40\trotated=false\tbytes=220\n"
        "R\ta1\tboot-d\t1\t4500\tjournald\t5\tboot\t60\n"
    )
    (seg_dir / "50-partial.jseg").write_bytes(
        b"JSEG\tname=partial\tseq=50\trotated=true\tbytes=210\n"
        b"R\tp1\tboot-a\t30\t3020\tapp\t5\tcommitted\t60\n"
        b"R\tp2\tboot-a\t31\t3030\tapp"
    )
    (seg_dir / "60-invalid.jseg").write_text(
        "JSEG\tname=broken\tseq=nope\trotated=true\tbytes=99\nR\tx\tboot-x\t1\t1\tu\t1\tm\t1\n"
    )
    before = file_digest_tree(case_dir)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "old-vacuum.tsv").write_text("stale\n", encoding="utf-8")
    run_auditor(case_dir, out_dir)
    assert_reports_match(case_dir, out_dir)
    assert file_digest_tree(case_dir) == before


def test_malformed_tail_stops_later_record_scan(tmp_path):
    """A malformed record line counts once and later valid-looking bytes from that segment stay uncommitted."""
    case_dir = tmp_path / "case"
    seg_dir = case_dir / "segments"
    seg_dir.mkdir(parents=True)
    (case_dir / "policy.json").write_text(
        json.dumps(
            {
                "retain_boots": 1,
                "retain_realtime_after_us": 0,
                "max_bytes": 1000,
                "max_rotated_bytes": 1000,
            }
        )
    )
    (seg_dir / "tail-stop.jseg").write_text(
        "JSEG\tname=tail-stop\tseq=1\trotated=true\tbytes=120\n"
        "R\tkeep-1\tboot-a\t1\t100\tunit\t5\tcommitted\t20\n"
        "R\tbad\tboot-a\tmissing-fields\n"
        "R\tlater-looks-valid\tboot-z\t1\t900\tunit\t5\tmust-not-commit\t20\n"
    )
    out_dir = tmp_path / "out"
    run_auditor(case_dir, out_dir)
    assert_reports_match(case_dir, out_dir)

    summary = json.loads((out_dir / "summary.json").read_text())
    timeline = read_tsv(out_dir / "timeline.tsv")
    assert summary["ignored_tail_records"] == 1
    assert summary["committed_records"] == 1
    assert [row["cursor"] for row in timeline] == ["keep-1"]


def test_newest_boot_retention_does_not_need_realtime_cutoff(tmp_path):
    """A rotated segment from a newest retained boot is protected even when its records are older than the realtime cutoff."""
    case_dir = tmp_path / "case"
    seg_dir = case_dir / "segments"
    seg_dir.mkdir(parents=True)
    (case_dir / "policy.json").write_text(
        json.dumps(
            {
                "retain_boots": 1,
                "retain_realtime_after_us": 10000,
                "max_bytes": 1000,
                "max_rotated_bytes": 1000,
            }
        )
    )
    (seg_dir / "10-old.jseg").write_text(
        "JSEG\tname=old\tseq=10\trotated=true\tbytes=100\n"
        "R\told-1\tboot-old\t1\t100\tunit\t5\told\t10\n"
    )
    (seg_dir / "20-newest.jseg").write_text(
        "JSEG\tname=newest\tseq=20\trotated=true\tbytes=110\n"
        "R\tnew-1\tboot-new\t1\t200\tunit\t5\tnew\t10\n"
    )
    out_dir = tmp_path / "out"
    run_auditor(case_dir, out_dir)
    assert_reports_match(case_dir, out_dir)

    vacuum = {row["segment"]: row for row in read_tsv(out_dir / "vacuum.tsv")}
    assert vacuum["segments/20-newest.jseg"]["action"] == "keep"
    assert vacuum["segments/20-newest.jseg"]["reason"] == "retention-window"
    assert vacuum["segments/10-old.jseg"]["reason"] == "budget-spare"


def test_rotated_budget_can_force_deletion_when_total_budget_allows_keep(tmp_path):
    """The rotated-byte budget alone can require deleting eligible rotated candidates."""
    case_dir = tmp_path / "case"
    seg_dir = case_dir / "segments"
    seg_dir.mkdir(parents=True)
    (case_dir / "policy.json").write_text(
        json.dumps(
            {
                "retain_boots": 0,
                "retain_realtime_after_us": 10000,
                "max_bytes": 1000,
                "max_rotated_bytes": 250,
            }
        )
    )
    (seg_dir / "10-oldest.jseg").write_text(
        "JSEG\tname=oldest\tseq=10\trotated=true\tbytes=200\n"
        "R\trold\tboot-old\t1\t100\tunit\t5\told\t10\n"
    )
    (seg_dir / "20-newer.jseg").write_text(
        "JSEG\tname=newer\tseq=20\trotated=true\tbytes=200\n"
        "R\trnew\tboot-new\t1\t200\tunit\t5\tnew\t10\n"
    )
    (seg_dir / "30-active.jseg").write_text(
        "JSEG\tname=active\tseq=30\trotated=false\tbytes=300\n"
        "R\tactive\tboot-live\t1\t300\tunit\t5\tlive\t10\n"
    )
    out_dir = tmp_path / "out"
    run_auditor(case_dir, out_dir)
    assert_reports_match(case_dir, out_dir)

    summary = json.loads((out_dir / "summary.json").read_text())
    vacuum = {row["segment"]: row for row in read_tsv(out_dir / "vacuum.tsv")}
    assert summary["over_budget"] is False
    assert summary["total_bytes_after_plan"] == 500
    assert vacuum["segments/10-oldest.jseg"]["action"] == "delete"
    assert vacuum["segments/10-oldest.jseg"]["reason"] == "budget"
    assert vacuum["segments/20-newer.jseg"]["action"] == "keep"
    assert vacuum["segments/20-newer.jseg"]["reason"] == "budget-spare"
