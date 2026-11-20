import csv
import hashlib
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

APP = Path(os.environ.get("ABIGUARD_APP", "/app"))
POLICY = Path(os.environ.get("ABIGUARD_POLICY", "/etc/abiguard/policy.toml"))
BASELINE = Path(os.environ.get("ABIGUARD_BASELINE", "/var/lib/abiguard/baselines"))
RELEASE = Path(os.environ.get("ABIGUARD_RELEASE", "/var/lib/abiguard/releases/current"))
OUTPUT = Path(os.environ.get("ABIGUARD_OUTPUT", "/var/lib/abiguard/output"))
BIN = APP / "abiguard/bin/abiguard"
SRC = APP / "abiguard/src/main.cpp"
IMMUTABLE_PATHS = [
    POLICY,
    BASELINE / "libalpha.abi",
    BASELINE / "libbeta.abi",
    BASELINE / "libgamma.abi",
    APP / "abiguard/docs/abi_policy.md",
    APP / "abiguard/docs/manifest_format.md",
]


def run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_manifest(path):
    manifest = {"exports": [], "nodes": set(), "needs": [], "library": None, "soname": None}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "library":
            manifest["library"] = parts[1]
        elif parts[0] == "soname":
            manifest["soname"] = parts[1]
        elif parts[0] == "export":
            manifest["exports"].append((parts[1], parts[2], parts[3], len(parts) > 4 and parts[4] == "allow"))
        elif parts[0] == "version_node":
            manifest["nodes"].add(parts[1])
        elif parts[0] == "need_version":
            manifest["needs"].append((parts[1], parts[2]))
    assert manifest["library"] and manifest["soname"], f"bad manifest {path}"
    return manifest


def read_soname(lib):
    out = run(["readelf", "-d", str(lib)]).stdout
    marker = "Library soname: ["
    if marker not in out:
        return Path(lib).name
    return out.split(marker, 1)[1].split("]", 1)[0]


def split_version(raw):
    if "@@" in raw:
        name, version = raw.split("@@", 1)
        return name, version, "default"
    if "@" in raw:
        name, version = raw.split("@", 1)
        return name, version, "nondefault"
    return raw, "", "default"


def inspect(lib):
    exports = set()
    dyn = run(["readelf", "--dyn-syms", "--wide", str(lib)]).stdout
    for line in dyn.splitlines():
        parts = line.split()
        if len(parts) < 8 or not parts[0].endswith(":"):
            continue
        typ, bind, ndx, raw = parts[3], parts[4], parts[6], parts[7]
        if ndx in {"UND", "ABS"} or typ not in {"FUNC", "OBJECT"} or bind not in {"GLOBAL", "WEAK"}:
            continue
        if raw.startswith("_"):
            continue
        exports.add(split_version(raw))
    nodes = set()
    needs = set()
    current_file = None
    ver = run(["readelf", "--version-info", "--wide", str(lib)]).stdout
    for line in ver.splitlines():
        if "Version definition" in line or "Version needs" in line:
            current_file = None
        if "File:" in line:
            current_file = line.split("File:", 1)[1].strip().split()[0]
        if "Name:" in line:
            version = line.split("Name:", 1)[1].strip().split()[0]
            if current_file:
                needs.add((current_file, version))
            elif not version.startswith("GLIBC_") and version != read_soname(lib):
                nodes.add(version)
    return {"soname": read_soname(lib), "exports": exports, "nodes": nodes, "needs": needs}


def expected_for(baseline_dir, release_dir):
    issues = []
    for manifest_path in sorted(Path(baseline_dir).glob("*.abi")):
        m = parse_manifest(manifest_path)
        obs = inspect(Path(release_dir) / m["library"])
        if obs["soname"] != m["soname"]:
            issues.append((m["library"], "SONAME_BREAK", "", obs["soname"], True))
        for node in sorted(m["nodes"] - obs["nodes"]):
            issues.append((m["library"], "MISSING_VERSION_NODE", "", node, True))
        for name, version, mode, allow in m["exports"]:
            if (name, version, mode) not in obs["exports"]:
                replacement = sorted(v for n, v, md in obs["exports"] if n == name and md == mode)
                if mode == "default" and replacement:
                    issues.append((m["library"], "DEFAULT_VERSION_CHANGED", name, replacement[0], True))
                elif allow:
                    issues.append((m["library"], "ALLOWLISTED_BREAK", name, version, False))
                else:
                    issues.append((m["library"], "REMOVED_EXPORT", name, version, True))
        baseline_exports = {(name, version, mode) for name, version, mode, _ in m["exports"]}
        baseline_symbol_modes = {(name, mode) for name, _, mode, _ in m["exports"]}
        for name, version, mode in sorted(obs["exports"] - baseline_exports):
            if (name, mode) in baseline_symbol_modes:
                continue
            issues.append((m["library"], "UNDOCUMENTED_GLOBAL", name, version, True))
        baseline_needs = set(m["needs"])
        for needed, version in sorted(obs["needs"] - baseline_needs):
            if not version.startswith("GLIBC_"):
                issues.append((m["library"], "NEW_REQUIRED_DEP_VERSION", needed, version, True))
    return sorted(issues)


def run_abiguard(output=OUTPUT, baseline=BASELINE, release=RELEASE):
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    run([str(BIN), "--policy", str(POLICY), "--baseline", str(baseline), "--release", str(release), "--output", str(output)])
    return output


def load_trace(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def actual_issue_tuples(output):
    return sorted((r["library"], r["code"], r.get("symbol", ""), r.get("version", ""), bool(r["blocking"])) for r in load_trace(Path(output) / "risk_trace.jsonl"))


def test_rebuilds_and_writes_required_outputs():
    """Verify the C++ project rebuilds and the documented CLI writes all required report files."""
    run(["make", "clean", "all"], cwd=APP / "abiguard")
    run_abiguard()
    for name in ["abi_report.json", "soname_matrix.csv", "risk_trace.jsonl"]:
        path = OUTPUT / name
        assert path.exists(), f"missing {path}"
        assert path.stat().st_size > 0, f"empty {path}"


def test_immutable_inputs_are_not_modified():
    """Verify baseline, release policy, and task documentation inputs retain their shipped digests."""
    before = {str(p): sha256(p) for p in IMMUTABLE_PATHS}
    run_abiguard()
    after = {str(p): sha256(p) for p in IMMUTABLE_PATHS}
    assert after == before


def test_report_schema_and_summary_counts():
    """Verify JSON, CSV, and JSONL reports have stable schemas and internally consistent counts."""
    run_abiguard()
    report = json.loads((OUTPUT / "abi_report.json").read_text())
    trace = load_trace(OUTPUT / "risk_trace.jsonl")
    assert set(report) == {"summary", "libraries"}
    assert report["summary"]["libraries"] == 3
    assert report["summary"]["issues"] == len(trace)
    assert report["summary"]["blocking"] == sum(1 for row in trace if row["blocking"])
    with (OUTPUT / "soname_matrix.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows and set(rows[0]) == {"library", "expected_soname", "actual_soname", "status"}
    assert [r["library"] for r in rows] == sorted(r["library"] for r in rows)


def test_fixture_semantics_match_reference():
    """Compare shipped release findings against an independent readelf-based reference."""
    run_abiguard()
    expected = expected_for(BASELINE, RELEASE)
    actual = actual_issue_tuples(OUTPUT)
    assert actual == expected
    codes = {code for _, code, _, _, _ in actual}
    assert {"REMOVED_EXPORT", "DEFAULT_VERSION_CHANGED", "MISSING_VERSION_NODE", "SONAME_BREAK", "NEW_REQUIRED_DEP_VERSION", "UNDOCUMENTED_GLOBAL", "ALLOWLISTED_BREAK"} <= codes
    assert ("libbeta.so", "UNDOCUMENTED_GLOBAL", "optional_hook", "", True) not in actual


def test_outputs_are_byte_deterministic():
    """Run the CLI twice and require byte-identical outputs."""
    first = Path("/tmp/abiguard-first")
    second = Path("/tmp/abiguard-second")
    run_abiguard(first)
    run_abiguard(second)
    for name in ["abi_report.json", "soname_matrix.csv", "risk_trace.jsonl"]:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def build_holdout(tmp_path):
    """Compile an unseen release fixture and matching baseline manifest for data-driven checks."""
    src = tmp_path / "src"
    rel = tmp_path / "release"
    base = tmp_path / "baseline"
    src.mkdir()
    rel.mkdir()
    base.mkdir()
    (src / "dep.c").write_text(
        "int dep_old(void){return 1;} int dep_future(void){return 2;}\n"
    )
    (src / "dep.map").write_text(
        "HDEP_1.0 { global: dep_old; local: *; };\n"
        "HDEP_3.0 { global: dep_future; } HDEP_1.0;\n"
    )
    (src / "hold.c").write_text(
        "extern int dep_future(void); "
        "extern void maybe_missing(void) __attribute__((weak)); "
        "int hold_open(void){return dep_future();} "
        "int hold_extra(void){return maybe_missing?3:4;}\n"
    )
    (src / "hold.map").write_text("HOLD_2.0 { global: hold_open; hold_extra; local: *; };\n")
    run(["cc", "-fPIC", "-shared", str(src / "dep.c"), "-Wl,--version-script," + str(src / "dep.map"), "-Wl,-soname,libhdep.so.1", "-o", str(rel / "libhdep.so")])
    run(["cc", "-fPIC", "-shared", str(src / "hold.c"), "-L" + str(rel), "-lhdep", "-Wl,--version-script," + str(src / "hold.map"), "-Wl,-soname,libhold.so.2", "-Wl,-rpath,$ORIGIN", "-o", str(rel / "libhold.so")])
    (base / "libhold.abi").write_text(textwrap.dedent("""
        library libhold.so
        soname libhold.so.1
        export hold_open HOLD_1.0 default
        export hold_removed HOLD_1.0 default
        version_node HOLD_1.0
        need_version libhdep.so.1 HDEP_1.0
    """).strip() + "\n")
    return base, rel


def test_holdout_release_is_data_driven(tmp_path):
    """Build an unseen ELF release under /tmp and verify the same CLI handles changed fixtures."""
    base, rel = build_holdout(tmp_path)
    out = tmp_path / "out"
    run_abiguard(out, base, rel)
    assert actual_issue_tuples(out) == expected_for(base, rel)
    codes = {code for _, code, _, _, _ in actual_issue_tuples(out)}
    assert {"SONAME_BREAK", "DEFAULT_VERSION_CHANGED", "REMOVED_EXPORT", "MISSING_VERSION_NODE", "NEW_REQUIRED_DEP_VERSION", "UNDOCUMENTED_GLOBAL"} <= codes
