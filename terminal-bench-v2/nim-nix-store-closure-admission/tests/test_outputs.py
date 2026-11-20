import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/app/storegate")
BIN = ROOT / "bin" / "storegate"
CANDIDATE_USER = "agent"
RUNUSER = Path("/usr/sbin/runuser")


def candidate_account():
    try:
        return pwd.getpwnam(CANDIDATE_USER)
    except KeyError:
        return None


def prepare_candidate_paths(*paths):
    account = candidate_account()
    if os.geteuid() != 0 or account is None:
        return
    seen = set()
    for raw in paths:
        path = Path(raw)
        target = path if path.exists() else path.parent
        if not target.exists():
            continue
        for parent in target.resolve().parents:
            if parent == Path("/tmp"):
                break
            os.chmod(parent, os.stat(parent).st_mode | 0o111)
        for current, dirs, files in os.walk(target):
            current_path = Path(current)
            if current_path in seen:
                continue
            seen.add(current_path)
            os.chown(current_path, account.pw_uid, account.pw_gid)
            os.chmod(current_path, 0o700)
            for dirname in dirs:
                child = current_path / dirname
                os.chown(child, account.pw_uid, account.pw_gid)
                os.chmod(child, 0o700)
            for filename in files:
                child = current_path / filename
                mode = child.stat().st_mode
                os.chown(child, account.pw_uid, account.pw_gid)
                os.chmod(child, 0o700 if mode & 0o111 else 0o600)


def run_as_candidate(argv, *, cwd, **kwargs):
    check = kwargs.pop("check", False)
    if os.geteuid() != 0:
        return subprocess.run(argv, cwd=cwd, check=check, **kwargs)
    assert candidate_account() is not None, f"missing unprivileged {CANDIDATE_USER} account"
    assert RUNUSER.exists(), "runuser is required to execute candidate code unprivileged"
    tests_dir = Path("/tests")
    original_mode = None
    if tests_dir.exists():
        original_mode = tests_dir.stat().st_mode & 0o777
        os.chmod(tests_dir, 0o700)
    try:
        return subprocess.run([str(RUNUSER), "-u", CANDIDATE_USER, "--", *argv], cwd=cwd, check=check, **kwargs)
    finally:
        if original_mode is not None:
            os.chmod(tests_dir, original_mode)


def write_case(base, derivations, substitutes):
    case = base / "case"
    case.mkdir(parents=True)
    with (case / "derivations.ndjson").open("w", encoding="utf-8") as fh:
        for item in derivations:
            fh.write(json.dumps(item, sort_keys=True) + "\n")
    with (case / "substitutes.ndjson").open("w", encoding="utf-8") as fh:
        for item in substitutes:
            fh.write(json.dumps(item, sort_keys=True) + "\n")
    return case


def build_tool():
    proc = run_as_candidate(["/app/storegate/scripts/build.sh"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert BIN.exists()


def run_gate(case, out, platform="x86_64-linux", features="", roots=""):
    prepare_candidate_paths(case.parent, out.parent)
    return run_as_candidate(
        [str(BIN), "--case", str(case), "--platform", platform, "--features", features, "--roots", roots, "--out", str(out)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def load_outputs(out):
    plan_path = out / "plan.json"
    audit_path = out / "audit.ndjson"
    assert sorted(p.name for p in out.iterdir()) == ["audit.ndjson", "plan.json"]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    audit_lines = [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line]
    assert audit_lines == sorted(audit_lines)
    audit = [json.loads(line) for line in audit_lines]
    return plan, audit


def topo_ok(plan, edges):
    first = {}
    for i, step in enumerate(plan):
        first.setdefault(step["derivation"], i)
    for dep, user in edges:
        assert first[dep] < first[user]


def audit_pairs(audit, event):
    return {(e.get("id"), e.get("path") or e.get("reason")) for e in audit if e.get("event") == event}


def test_admission_acceptance_case_a(tmp_path):
    """Verifies closure order, fixed-output deduplication, substitutes, and audit rows."""
    build_tool()
    derivations = [
        {"id": "libA", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/100-libA", "hash": "h-libA", "fixed": False}]},
        {"id": "fetchA", "platforms": ["x86_64-linux"], "features": ["ca"], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/300-fetchA", "hash": "h-fixed", "fixed": True}]},
        {"id": "fetchB", "platforms": ["x86_64-linux"], "features": ["ca", "sandbox"], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/200-fetchB", "hash": "h-fixed", "fixed": True}]},
        {"id": "appA", "platforms": ["x86_64-linux"], "features": ["sandbox"], "inputs": ["libA", "fetchA", "fetchB"], "outputs": [{"name": "out", "path": "/nix/store/900-appA", "hash": "h-appA", "fixed": False}]},
        {"id": "toolB", "platforms": ["x86_64-linux"], "features": [], "inputs": ["libA"], "outputs": [{"name": "out", "path": "/nix/store/800-toolB", "hash": "h-toolB", "fixed": False}]},
    ]
    substitutes = [
        {"path": "/nix/store/300-fetchA", "hash": "h-fixed", "source": "older", "priority": 10},
        {"path": "/nix/store/not-chosen", "hash": "h-fixed", "source": "cache-z", "priority": 40},
        {"path": "/nix/store/200-fetchB", "hash": "h-fixed", "source": "cache-a", "priority": 40},
        {"path": "/nix/store/800-toolB", "hash": "wrong", "source": "bad", "priority": 99},
        {"path": "/nix/store/800-toolB", "hash": "h-toolB", "source": "cache-b", "priority": 11},
        {"path": "/nix/store/800-toolB", "hash": "h-toolB", "source": "cache-a", "priority": 11},
    ]
    case = write_case(tmp_path, derivations, substitutes)
    out = tmp_path / "out"
    rc = run_gate(case, out, features="sandbox,ca", roots="appA,toolB")
    assert rc.returncode == 0, rc.stderr
    plan, audit = load_outputs(out)
    assert plan == [
        {"path": "/nix/store/100-libA", "derivation": "libA", "output": "out", "hash": "h-libA", "method": "build", "source": "local"},
        {"path": "/nix/store/200-fetchB", "derivation": "fetchB", "output": "out", "hash": "h-fixed", "method": "substitute", "source": "cache-a"},
        {"path": "/nix/store/800-toolB", "derivation": "toolB", "output": "out", "hash": "h-toolB", "method": "substitute", "source": "cache-a"},
        {"path": "/nix/store/900-appA", "derivation": "appA", "output": "out", "hash": "h-appA", "method": "build", "source": "local"},
    ]
    topo_ok(plan, [("libA", "appA"), ("fetchB", "appA"), ("libA", "toolB")])
    assert [e["event"] for e in audit].count("root") == 2
    assert any(e.get("event") == "substitute" and e.get("path") == "/nix/store/200-fetchB" for e in audit)


def test_admission_rejection_case_b(tmp_path):
    """Verifies denial output, exhaustive reasons, and deterministic audit fields."""
    build_tool()
    derivations = [
        {"id": "alpha", "platforms": ["x86_64-linux"], "features": ["sandbox"], "inputs": ["beta"], "outputs": [{"name": "out", "path": "/nix/store/a-alpha", "hash": "ha", "fixed": False}]},
        {"id": "beta", "platforms": ["x86_64-linux"], "features": [], "inputs": ["gamma"], "outputs": [{"name": "out", "path": "/nix/store/b-beta", "hash": "hb", "fixed": False}]},
        {"id": "gamma", "platforms": ["aarch64-linux"], "features": ["kvm"], "inputs": ["alpha"], "outputs": [{"name": "out", "path": "/nix/store/c-gamma", "hash": "hc", "fixed": False}]},
        {"id": "delta", "platforms": ["x86_64-linux"], "features": [], "inputs": ["missing"], "outputs": [{"name": "out", "path": "/nix/store/d-delta", "hash": "hd", "fixed": False}]},
    ]
    case = write_case(tmp_path, derivations, [])
    out = tmp_path / "out"
    rc = run_gate(case, out, features="sandbox", roots="alpha,delta")
    assert rc.returncode != 0
    plan, audit = load_outputs(out)
    assert plan == []
    denials = {(e.get("id"), e.get("reason")) for e in audit if e.get("event") == "deny"}
    assert ("alpha", "cycle") in denials or ("beta", "cycle") in denials or ("gamma", "cycle") in denials
    assert ("gamma", "platform") in denials
    assert ("gamma", "feature") in denials
    assert ("missing", "unknown") in denials


def test_admission_case_c(tmp_path):
    """Verifies stale-output cleanup and repeatable fixed-output admission results."""
    build_tool()
    derivations = [
        {"id": "base", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/010-base", "hash": "hb", "fixed": False}]},
        {"id": "asset1", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/070-asset1", "hash": "hasset", "fixed": True}]},
        {"id": "asset2", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/060-asset2", "hash": "hasset", "fixed": True}]},
        {"id": "leaf", "platforms": ["x86_64-linux"], "features": [], "inputs": ["asset1", "asset2", "base"], "outputs": [{"name": "out", "path": "/nix/store/900-leaf", "hash": "hl", "fixed": False}]},
    ]
    substitutes = [
        {"path": "/nix/store/060-asset2", "hash": "hasset", "source": "near", "priority": 7},
        {"path": "/nix/store/010-base", "hash": "hb", "source": "mirror", "priority": 3},
    ]
    case = write_case(tmp_path, derivations, substitutes)
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.txt").write_text("old", encoding="utf-8")
    first = run_gate(case, out, features="", roots="leaf")
    assert first.returncode == 0, first.stderr
    first_plan = (out / "plan.json").read_text(encoding="utf-8")
    first_audit = (out / "audit.ndjson").read_text(encoding="utf-8")
    assert "stale.txt" not in {p.name for p in out.iterdir()}
    shutil.rmtree(out)
    second = run_gate(case, out, features="", roots="leaf")
    assert second.returncode == 0, second.stderr
    assert first_plan == (out / "plan.json").read_text(encoding="utf-8")
    assert first_audit == (out / "audit.ndjson").read_text(encoding="utf-8")
    plan, audit = load_outputs(out)
    assert [s["path"] for s in plan] == ["/nix/store/010-base", "/nix/store/060-asset2", "/nix/store/900-leaf"]
    assert any(e.get("event") == "substitute" and e.get("path") == "/nix/store/060-asset2" for e in audit)


def test_layered_fixed_output_and_multi_output_ordering(tmp_path):
    """Verifies fixed-output ownership, multi-output sorting, substitute ties, and nested cleanup."""
    build_tool()
    derivations = [
        {"id": "rootz", "platforms": ["x86_64-linux"], "features": ["ssl", "jit"], "inputs": ["tool", "fetch-hi", "fetch-low"], "outputs": [
            {"name": "bin", "path": "/nix/store/900-rootz-bin", "hash": "h-rootz-bin", "fixed": False},
            {"name": "dev", "path": "/nix/store/850-rootz-dev", "hash": "h-rootz-dev", "fixed": False},
        ]},
        {"id": "tool", "platforms": ["x86_64-linux"], "features": [], "inputs": ["base"], "outputs": [
            {"name": "lib", "path": "/nix/store/120-tool-lib", "hash": "h-tool-lib", "fixed": False},
            {"name": "out", "path": "/nix/store/200-tool-out", "hash": "h-tool-out", "fixed": False},
        ]},
        {"id": "base", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/010-base", "hash": "h-base", "fixed": False}]},
        {"id": "fetch-hi", "platforms": ["x86_64-linux"], "features": ["ssl"], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/080-fetch-hi", "hash": "h-fixed-z", "fixed": True}]},
        {"id": "fetch-low", "platforms": ["x86_64-linux"], "features": ["jit"], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/040-fetch-low", "hash": "h-fixed-z", "fixed": True}]},
    ]
    substitutes = [
        {"path": "/nix/store/unrelated-fixed", "hash": "h-fixed-z", "source": "cache-b", "priority": 99},
        {"path": "/nix/store/040-fetch-low", "hash": "h-fixed-z", "source": "cache-a", "priority": 99},
        {"path": "/nix/store/120-tool-lib", "hash": "h-tool-lib", "source": "zz", "priority": 5},
        {"path": "/nix/store/120-tool-lib", "hash": "wrong", "source": "aa", "priority": 500},
        {"path": "/nix/store/200-tool-out", "hash": "h-tool-out", "source": "mid", "priority": 5},
    ]
    case = write_case(tmp_path, derivations, substitutes)
    out = tmp_path / "out"
    (out / "nested" / "old").mkdir(parents=True)
    (out / "nested" / "old" / "junk.txt").write_text("stale", encoding="utf-8")

    rc = run_gate(case, out, features="jit,ssl", roots="rootz")
    assert rc.returncode == 0, rc.stderr
    plan, audit = load_outputs(out)
    assert [step["path"] for step in plan] == [
        "/nix/store/010-base",
        "/nix/store/040-fetch-low",
        "/nix/store/120-tool-lib",
        "/nix/store/200-tool-out",
        "/nix/store/850-rootz-dev",
        "/nix/store/900-rootz-bin",
    ]
    assert [step["output"] for step in plan if step["derivation"] == "rootz"] == ["dev", "bin"]
    fixed = next(step for step in plan if step["hash"] == "h-fixed-z")
    assert fixed == {"path": "/nix/store/040-fetch-low", "derivation": "fetch-low", "output": "out", "hash": "h-fixed-z", "method": "substitute", "source": "cache-a"}
    assert audit_pairs(audit, "admit") >= {("base", "/nix/store/010-base"), ("fetch-low", "/nix/store/040-fetch-low"), ("rootz", "/nix/store/850-rootz-dev")}
    assert (out / "nested").exists() is False


def test_canonical_fixed_owner_remains_a_dependency_barrier(tmp_path):
    """Verifies that fixed-output deduplication rewires dependent groups without losing ordering."""
    build_tool()
    derivations = [
        {"id": "base", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/010-base", "hash": "h-base", "fixed": False}]},
        {"id": "consumer", "platforms": ["x86_64-linux"], "features": [], "inputs": ["base", "fetch-wide"], "outputs": [{"name": "out", "path": "/nix/store/020-consumer", "hash": "h-consumer", "fixed": False}]},
        {"id": "fetch-small", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/030-fetch-small", "hash": "h-blob", "fixed": True}]},
        {"id": "fetch-wide", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/700-fetch-wide", "hash": "h-blob", "fixed": True}]},
        {"id": "root", "platforms": ["x86_64-linux"], "features": [], "inputs": ["consumer", "fetch-small"], "outputs": [{"name": "out", "path": "/nix/store/900-root", "hash": "h-root", "fixed": False}]},
    ]
    substitutes = [
        {"path": "/nix/store/030-fetch-small", "hash": "h-blob", "source": "chosen", "priority": 1},
        {"path": "/nix/store/700-fetch-wide", "hash": "h-blob", "source": "ignored", "priority": 99},
    ]
    case = write_case(tmp_path, derivations, substitutes)
    out = tmp_path / "out"
    rc = run_gate(case, out, roots="root")
    assert rc.returncode == 0, rc.stderr
    plan, audit = load_outputs(out)
    assert [step["path"] for step in plan] == [
        "/nix/store/010-base",
        "/nix/store/030-fetch-small",
        "/nix/store/020-consumer",
        "/nix/store/900-root",
    ]
    assert all(step["path"] != "/nix/store/700-fetch-wide" for step in plan)
    assert next(step for step in plan if step["hash"] == "h-blob") == {
        "path": "/nix/store/030-fetch-small",
        "derivation": "fetch-small",
        "output": "out",
        "hash": "h-blob",
        "method": "substitute",
        "source": "ignored",
    }
    topo_ok(plan, [("base", "consumer"), ("fetch-small", "consumer"), ("consumer", "root")])
    assert audit_pairs(audit, "substitute") == {("fetch-small", "/nix/store/030-fetch-small")}


def test_path_inputs_coalesce_and_preserve_ready_group_order(tmp_path):
    """Verifies output-path inputs, canonical fixed-output ownership, and ready-group tie breaks."""
    build_tool()
    derivations = [
        {"id": "alpha", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/040-alpha", "hash": "h-alpha", "fixed": False}]},
        {"id": "beta", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/020-beta", "hash": "h-beta", "fixed": False}]},
        {"id": "fetch-late", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/700-fetch-late", "hash": "h-payload", "fixed": True}]},
        {"id": "fetch-early", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/030-fetch-early", "hash": "h-payload", "fixed": True}]},
        {"id": "consumer", "platforms": ["x86_64-linux"], "features": [], "inputs": ["/nix/store/700-fetch-late", "/nix/store/040-alpha"], "outputs": [{"name": "out", "path": "/nix/store/050-consumer", "hash": "h-consumer", "fixed": False}]},
        {"id": "root", "platforms": ["x86_64-linux"], "features": [], "inputs": ["consumer", "beta", "fetch-early"], "outputs": [{"name": "out", "path": "/nix/store/900-root", "hash": "h-root", "fixed": False}]},
    ]
    substitutes = [
        {"path": "/nix/store/700-fetch-late", "hash": "h-payload", "source": "late-cache", "priority": 90},
        {"path": "/nix/store/030-fetch-early", "hash": "h-payload", "source": "early-cache", "priority": 5},
    ]
    case = write_case(tmp_path, derivations, substitutes)
    out = tmp_path / "out"
    rc = run_gate(case, out, roots="root")
    assert rc.returncode == 0, rc.stderr
    plan, audit = load_outputs(out)
    assert [step["path"] for step in plan] == [
        "/nix/store/020-beta",
        "/nix/store/030-fetch-early",
        "/nix/store/040-alpha",
        "/nix/store/050-consumer",
        "/nix/store/900-root",
    ]
    assert all(step["path"] != "/nix/store/700-fetch-late" for step in plan)
    assert next(step for step in plan if step["hash"] == "h-payload") == {
        "path": "/nix/store/030-fetch-early",
        "derivation": "fetch-early",
        "output": "out",
        "hash": "h-payload",
        "method": "substitute",
        "source": "late-cache",
    }
    topo_ok(plan, [("fetch-early", "consumer"), ("alpha", "consumer"), ("consumer", "root"), ("beta", "root")])
    assert audit_pairs(audit, "admit") >= {("fetch-early", "/nix/store/030-fetch-early"), ("consumer", "/nix/store/050-consumer")}


def test_feature_requirements_are_all_or_nothing(tmp_path):
    """Verifies that every declared feature must be enabled and denials remain parseable."""
    build_tool()
    derivations = [
        {"id": "ok-base", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/010-ok-base", "hash": "h-ok", "fixed": False}]},
        {"id": "needs-two", "platforms": ["x86_64-linux"], "features": ["ssl", "jit"], "inputs": ["ok-base"], "outputs": [{"name": "out", "path": "/nix/store/500-needs-two", "hash": "h-two", "fixed": False}]},
    ]
    case = write_case(tmp_path, derivations, [])
    out = tmp_path / "out"
    rc = run_gate(case, out, features="ssl", roots="needs-two")
    assert rc.returncode != 0
    plan, audit = load_outputs(out)
    assert plan == []
    assert ("needs-two", "feature") in audit_pairs(audit, "deny")
    assert all("event" in e and "id" in e for e in audit)


def test_named_output_selectors_join_fixed_output_coalescing(tmp_path):
    """Verifies named-output inputs, canonical fixed ownership, and compatible substitutes."""
    build_tool()
    derivations = [
        {"id": "tool", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [
            {"name": "dev", "path": "/nix/store/040-tool-dev", "hash": "h-dev", "fixed": False},
            {"name": "out", "path": "/nix/store/090-tool-out", "hash": "h-out", "fixed": False},
        ]},
        {"id": "blob-large", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/880-blob-large", "hash": "h-shared", "fixed": True}]},
        {"id": "blob-small", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/030-blob-small", "hash": "h-shared", "fixed": True}]},
        {"id": "consumer", "platforms": ["x86_64-linux"], "features": ["ssl"], "inputs": ["tool:dev", "blob-large:out", "blob-small"], "outputs": [{"name": "out", "path": "/nix/store/900-consumer", "hash": "h-consumer", "fixed": False}]},
    ]
    substitutes = [
        {"path": "/nix/store/030-blob-small", "hash": "h-shared", "source": "wrong-platform", "priority": 100, "platforms": ["aarch64-linux"]},
        {"path": "/nix/store/880-blob-large", "hash": "h-shared", "source": "missing-feature", "priority": 90, "features": ["cuda"]},
        {"path": "/nix/store/880-blob-large", "hash": "h-shared", "source": "usable", "priority": 80, "features": ["ssl"], "platforms": ["x86_64-linux"]},
    ]
    case = write_case(tmp_path, derivations, substitutes)
    out = tmp_path / "out"
    rc = run_gate(case, out, features="ssl", roots="consumer")
    assert rc.returncode == 0, rc.stderr
    plan, audit = load_outputs(out)
    assert [step["path"] for step in plan] == [
        "/nix/store/030-blob-small",
        "/nix/store/040-tool-dev",
        "/nix/store/090-tool-out",
        "/nix/store/900-consumer",
    ]
    assert next(step for step in plan if step["hash"] == "h-shared") == {
        "path": "/nix/store/030-blob-small",
        "derivation": "blob-small",
        "output": "out",
        "hash": "h-shared",
        "method": "substitute",
        "source": "usable",
    }
    topo_ok(plan, [("blob-small", "consumer"), ("tool", "consumer")])
    assert audit_pairs(audit, "admit") >= {("blob-small", "/nix/store/030-blob-small"), ("tool", "/nix/store/040-tool-dev")}


def test_output_selector_denials_are_precise_and_global(tmp_path):
    """Verifies selector errors deny the request without losing other applicable reasons."""
    build_tool()
    derivations = [
        {"id": "ok", "platforms": ["x86_64-linux"], "features": [], "inputs": [], "outputs": [{"name": "out", "path": "/nix/store/010-ok", "hash": "h-ok", "fixed": False}]},
        {"id": "bad-platform", "platforms": ["aarch64-linux"], "features": ["gpu"], "inputs": ["ok:missing"], "outputs": [{"name": "out", "path": "/nix/store/020-bad", "hash": "h-bad", "fixed": False}]},
        {"id": "root", "platforms": ["x86_64-linux"], "features": [], "inputs": ["bad-platform", "ghost:out"], "outputs": [{"name": "out", "path": "/nix/store/900-root", "hash": "h-root", "fixed": False}]},
    ]
    case = write_case(tmp_path, derivations, [])
    out = tmp_path / "out"
    rc = run_gate(case, out, roots="root")
    assert rc.returncode != 0
    plan, audit = load_outputs(out)
    assert plan == []
    denials = audit_pairs(audit, "deny")
    assert ("bad-platform", "platform") in denials
    assert ("bad-platform", "feature") in denials
    assert ("ok:missing", "unknown") in denials
    assert ("ghost:out", "unknown") in denials
