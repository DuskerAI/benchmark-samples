import json
import os
import subprocess
from pathlib import Path

import pytest

APP = Path("/app/planner")
BIN = APP / "bin" / "eviction-planner"


def run(cmd, **kwargs):
    env = os.environ.copy()
    env.setdefault("COURSIER_CACHE", "/nonexistent")
    env.setdefault("SBT_OPTS", "-Dsbt.offline=true")
    return subprocess.run(cmd, text=True, capture_output=True, env=env, check=False, **kwargs)


def build_tool():
    result = run([str(APP / "scripts" / "build.sh")], cwd=str(APP), timeout=180)
    assert result.returncode == 0, result.stderr + result.stdout
    assert BIN.exists() and os.access(BIN, os.X_OK)


@pytest.fixture(scope="session", autouse=True)
def compiled_planner():
    """The verifier builds the command once before exercising repository behavior."""
    build_tool()


def write_tsv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ["\t".join(header)]
    data.extend("\t".join(str(row.get(col, "")) for col in header) for row in rows)
    path.write_text("\n".join(data) + "\n")


def run_case(tmp_path, modules, deps, requests, constraints=None, exclusions=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], modules)
    write_tsv(
        repo / "deps.tsv",
        [
            "from_org",
            "from_name",
            "from_scala_binary",
            "from_version",
            "dep_scope",
            "to_org",
            "to_name",
            "to_scala_binary",
            "version",
            "classifier",
            "platform",
        ],
        deps,
    )
    if constraints is not None:
        write_tsv(
            repo / "constraints.tsv",
            ["org", "name", "scala_binary", "classifier", "platform", "min_version", "max_version", "include_min", "include_max"],
            constraints,
        )
    if exclusions is not None:
        write_tsv(
            repo / "exclusions.tsv",
            [
                "from_org",
                "from_name",
                "from_scala_binary",
                "from_version",
                "to_org",
                "to_name",
                "to_scala_binary",
                "version",
                "classifier",
                "platform",
            ],
            exclusions,
        )
    request = tmp_path / "request.tsv"
    write_tsv(request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform", "policy"], requests)
    lock = tmp_path / "lock.json"
    audit = tmp_path / "audit.json"
    result = run(
        [str(BIN), "--repo", str(repo), "--request", str(request), "--lock", str(lock), "--audit", str(audit)],
        cwd=str(APP),
        timeout=90,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    first_lock = lock.read_text()
    first_audit = audit.read_text()
    result = run(
        [str(BIN), "--repo", str(repo), "--request", str(request), "--lock", str(lock), "--audit", str(audit)],
        cwd=str(APP),
        timeout=90,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert lock.read_text() == first_lock
    assert audit.read_text() == first_audit
    return json.loads(first_lock), json.loads(first_audit)


def write_aliases(repo, rows):
    write_tsv(
        repo / "aliases.tsv",
        ["alias_org", "alias_name", "alias_scala_binary", "org", "name", "scala_binary"],
        rows,
    )


def by_identity(lock):
    return {
        (a["org"], a["name"], a["scala_binary"], a["classifier"], a["platform"]): a
        for a in lock["artifacts"]
    }


def assert_failed_without_outputs(result, lock, audit):
    assert result.returncode != 0
    assert not lock.exists()
    assert not audit.exists()


def test_smoke_repository_builds_and_writes_contract_outputs(tmp_path):
    """The bundled repository can be built and planned with the public command contract."""
    work = tmp_path / "visible"
    work.mkdir()
    lock = work / "lock.json"
    audit = work / "audit.json"
    result = run(
        [
            str(BIN),
            "--repo",
            str(APP / "repo"),
            "--request",
            str(APP / "cases" / "smoke" / "request.tsv"),
            "--lock",
            str(lock),
            "--audit",
            str(audit),
        ],
        cwd=str(APP),
        timeout=90,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(lock.read_text())["schema"] == "sbt-eviction-plan/v1"
    assert json.loads(audit.read_text())["schema"] == "sbt-eviction-audit/v1"


def test_identity_versioning_and_eviction_are_resolved_together(tmp_path):
    """Cross-built identities remain separate while selected versions and evictions follow the version scheme."""
    modules = [
        {"org": "org.example", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.example", "name": "bridge", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.libs", "name": "codec", "scala_binary": "2.13", "version": "1.9.0", "version_scheme": "early-semver"},
        {"org": "org.libs", "name": "codec", "scala_binary": "2.13", "version": "1.10.0", "version_scheme": "early-semver"},
        {"org": "org.libs", "name": "codec", "scala_binary": "2.12", "version": "1.0.0", "version_scheme": "early-semver"},
    ]
    deps = [
        {"from_org": "org.example", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.libs", "to_name": "codec", "to_scala_binary": "2.13", "version": "1.9.0"},
        {"from_org": "org.example", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.example", "to_name": "bridge", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "org.example", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.libs", "to_name": "codec", "to_scala_binary": "2.12", "version": "1.0.0"},
        {"from_org": "org.example", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.libs", "to_name": "codec", "to_scala_binary": "2.13", "version": "1.10.0"},
    ]
    requests = [{"org": "org.example", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"}]
    lock, audit = run_case(tmp_path, modules, deps, requests)
    artifacts = by_identity(lock)
    assert artifacts[("org.libs", "codec", "2.13", "", "")]["version"] == "1.10.0"
    assert artifacts[("org.libs", "codec", "2.12", "", "")]["version"] == "1.0.0"
    evictions = audit["evictions"]
    assert any(e["name"] == "codec" and e["scala_binary"] == "2.13" and e["evicted"] == "1.9.0" and e["compatible"] is True for e in evictions)
    assert not any(e["name"] == "codec" and e["scala_binary"] == "2.12" for e in evictions)


def test_scheme_conflicts_classifier_platform_and_scopes_are_preserved(tmp_path):
    """Independent artifact variants and incompatible candidates are represented in both outputs."""
    modules = [
        {"org": "org.case", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.case", "name": "loader", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.case", "name": "engine", "scala_binary": "2.13", "version": "2.3.5", "version_scheme": "pvp"},
        {"org": "org.case", "name": "engine", "scala_binary": "2.13", "version": "2.4.0", "version_scheme": "pvp"},
        {"org": "org.case", "name": "probe", "scala_binary": "2.13", "version": "0.2.9", "version_scheme": "early-semver"},
        {"org": "org.case", "name": "probe", "scala_binary": "2.13", "version": "0.3.0", "version_scheme": "early-semver"},
        {"org": "org.case", "name": "nativekit", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
    ]
    deps = [
        {"from_org": "org.case", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "org.case", "to_name": "loader", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "org.case", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.case", "to_name": "engine", "to_scala_binary": "2.13", "version": "2.3.5"},
        {"from_org": "org.case", "from_name": "loader", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.case", "to_name": "engine", "to_scala_binary": "2.13", "version": "2.4.0"},
        {"from_org": "org.case", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.case", "to_name": "probe", "to_scala_binary": "2.13", "version": "0.2.9"},
        {"from_org": "org.case", "from_name": "loader", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.case", "to_name": "probe", "to_scala_binary": "2.13", "version": "0.3.0"},
        {"from_org": "org.case", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.case", "to_name": "nativekit", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "org.case", "from_name": "loader", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.case", "to_name": "nativekit", "to_scala_binary": "2.13", "version": "1.0.0", "classifier": "tests", "platform": "linux-x64"},
    ]
    requests = [{"org": "org.case", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "runtime"}]
    lock, audit = run_case(tmp_path, modules, deps, requests)
    artifacts = by_identity(lock)
    assert artifacts[("org.case", "nativekit", "2.13", "", "")]["version"] == "1.0.0"
    assert artifacts[("org.case", "nativekit", "2.13", "tests", "linux-x64")]["version"] == "1.0.0"
    assert artifacts[("org.case", "loader", "2.13", "", "")]["scope"] == "runtime"
    assert audit["status"] == "conflict"
    assert any(e["name"] == "engine" and e["scheme"] == "pvp" and e["compatible"] is False for e in audit["evictions"])
    assert any(e["name"] == "probe" and e["scheme"] == "early-semver" and e["compatible"] is False for e in audit["evictions"])


def test_plugin_bootstrap_cycles_terminate_and_sort_outputs(tmp_path):
    """Plugin reachability handles cyclic bootstrap metadata and emits canonical ordering."""
    modules = [
        {"org": "z.build", "name": "plug", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "a.build", "name": "bridge", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "m.build", "name": "shim", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "b.build", "name": "api", "scala_binary": "", "version": "3.0.0", "version_scheme": "semver-spec"},
    ]
    deps = [
        {"from_org": "z.build", "from_name": "plug", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "plugin", "to_org": "a.build", "to_name": "bridge", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "a.build", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "bootstrap", "to_org": "m.build", "to_name": "shim", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "m.build", "from_name": "shim", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "bootstrap", "to_org": "a.build", "to_name": "bridge", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "a.build", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "b.build", "to_name": "api", "to_scala_binary": "", "version": "3.0.0"},
    ]
    requests = [{"org": "z.build", "name": "plug", "scala_binary": "2.13", "version": "1.0.0", "scope": "plugin"}]
    lock, audit = run_case(tmp_path, modules, deps, requests)
    identities = [(a["org"], a["name"], a["scala_binary"], a["classifier"], a["platform"]) for a in lock["artifacts"]]
    assert identities == sorted(identities)
    artifacts = by_identity(lock)
    assert artifacts[("a.build", "bridge", "2.13", "", "")]["scope"] == "plugin"
    assert artifacts[("m.build", "shim", "2.13", "", "")]["scope"] == "plugin"
    assert artifacts[("b.build", "api", "", "", "")]["scope"] == "plugin"
    assert audit["evictions"] == []


def test_multi_root_scopes_join_and_evictions_are_ordered(tmp_path):
    """Repeated reachability from multiple roots joins scopes and orders audit evictions canonically."""
    modules = [
        {"org": "org.join", "name": "compile-root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.join", "name": "runtime-root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.join", "name": "runtime-bridge", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.libs", "name": "alpha", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.libs", "name": "alpha", "scala_binary": "2.13", "version": "1.5.0", "version_scheme": "always"},
        {"org": "org.libs", "name": "alpha", "scala_binary": "2.13", "version": "2.0.0", "version_scheme": "always"},
        {"org": "org.libs", "name": "beta", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.libs", "name": "beta", "scala_binary": "2.13", "version": "2.0.0", "version_scheme": "always"},
    ]
    deps = [
        {"from_org": "org.join", "from_name": "compile-root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.libs", "to_name": "alpha", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "org.join", "from_name": "compile-root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.libs", "to_name": "alpha", "to_scala_binary": "2.13", "version": "1.5.0"},
        {"from_org": "org.join", "from_name": "compile-root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.libs", "to_name": "beta", "to_scala_binary": "2.13", "version": "2.0.0"},
        {"from_org": "org.join", "from_name": "runtime-root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "org.join", "to_name": "runtime-bridge", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "org.join", "from_name": "runtime-bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.libs", "to_name": "alpha", "to_scala_binary": "2.13", "version": "2.0.0"},
        {"from_org": "org.join", "from_name": "runtime-bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.libs", "to_name": "beta", "to_scala_binary": "2.13", "version": "1.0.0"},
    ]
    requests = [
        {"org": "org.join", "name": "compile-root", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
        {"org": "org.join", "name": "runtime-root", "scala_binary": "2.13", "version": "1.0.0", "scope": "runtime"},
    ]
    lock, audit = run_case(tmp_path, modules, deps, requests)
    artifacts = by_identity(lock)
    assert artifacts[("org.libs", "alpha", "2.13", "", "")]["version"] == "2.0.0"
    assert artifacts[("org.libs", "alpha", "2.13", "", "")]["scope"] == "compile,runtime"
    assert [(e["org"], e["name"], e["evicted"]) for e in audit["evictions"]] == [
        ("org.libs", "alpha", "1.0.0"),
        ("org.libs", "alpha", "1.5.0"),
        ("org.libs", "beta", "1.0.0"),
    ]


def test_compile_runtime_cycles_are_rejected_but_outputs_remain_atomic(tmp_path):
    """Non-plugin cycles are invalid and must not leave partially published evidence."""
    repo = tmp_path / "repo"
    repo.mkdir()
    modules = [
        {"org": "cycle", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "cycle", "name": "left", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "cycle", "name": "right", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
    ]
    deps = [
        {"from_org": "cycle", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "cycle", "to_name": "left", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "cycle", "from_name": "left", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "cycle", "to_name": "right", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "cycle", "from_name": "right", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "cycle", "to_name": "left", "to_scala_binary": "2.13", "version": "1.0.0"},
    ]
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], modules)
    write_tsv(repo / "deps.tsv", ["from_org", "from_name", "from_scala_binary", "from_version", "dep_scope", "to_org", "to_name", "to_scala_binary", "version", "classifier", "platform"], deps)
    request = tmp_path / "request.tsv"
    write_tsv(request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform"], [
        {"org": "cycle", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "runtime"},
    ])
    lock = tmp_path / "lock.json"
    audit = tmp_path / "audit.json"
    result = run([str(BIN), "--repo", str(repo), "--request", str(request), "--lock", str(lock), "--audit", str(audit)], cwd=str(APP), timeout=90)
    assert_failed_without_outputs(result, lock, audit)


def test_invalid_metadata_fails_without_complete_outputs(tmp_path):
    """Invalid component references return nonzero and do not publish lock or audit JSON."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], [])
    write_tsv(repo / "deps.tsv", ["from_org", "from_name", "from_scala_binary", "from_version", "dep_scope", "to_org", "to_name", "to_scala_binary", "version", "classifier", "platform"], [])
    request = tmp_path / "request.tsv"
    write_tsv(request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform"], [{"org": "missing", "name": "root", "version": "1.0.0", "scope": "compile"}])
    lock = tmp_path / "lock.json"
    audit = tmp_path / "audit.json"
    result = run([str(BIN), "--repo", str(repo), "--request", str(request), "--lock", str(lock), "--audit", str(audit)], cwd=str(APP), timeout=90)
    assert result.returncode != 0
    assert not lock.exists()
    assert not audit.exists()


def test_missing_required_inputs_fail_but_missing_deps_is_header_only(tmp_path):
    """Required files are atomic failures, while an absent dependency file behaves like an empty dependency table."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], [
        {"org": "headers", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
    ])
    request = tmp_path / "request.tsv"
    write_tsv(request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform"], [
        {"org": "headers", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
    ])
    lock = tmp_path / "lock.json"
    audit = tmp_path / "audit.json"
    result = run([str(BIN), "--repo", str(repo), "--request", str(request), "--lock", str(lock), "--audit", str(audit)], cwd=str(APP), timeout=90)
    assert result.returncode == 0, result.stderr + result.stdout
    assert by_identity(json.loads(lock.read_text()))[("headers", "root", "2.13", "", "")]["version"] == "1.0.0"

    missing_request = tmp_path / "does-not-exist.tsv"
    missing_lock = tmp_path / "missing-lock.json"
    missing_audit = tmp_path / "missing-audit.json"
    result = run([str(BIN), "--repo", str(repo), "--request", str(missing_request), "--lock", str(missing_lock), "--audit", str(missing_audit)], cwd=str(APP), timeout=90)
    assert_failed_without_outputs(result, missing_lock, missing_audit)

    missing_repo = tmp_path / "missing-repo"
    repo_lock = tmp_path / "repo-lock.json"
    repo_audit = tmp_path / "repo-audit.json"
    result = run([str(BIN), "--repo", str(missing_repo), "--request", str(request), "--lock", str(repo_lock), "--audit", str(repo_audit)], cwd=str(APP), timeout=90)
    assert_failed_without_outputs(result, repo_lock, repo_audit)


def test_request_overrides_force_selection_and_deny_candidates(tmp_path):
    """Request override policy affects transitive candidates, evictions, and invalid denied-only identities."""
    modules = [
        {"org": "org.policy", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.policy", "name": "bridge", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.policy", "name": "api", "scala_binary": "2.13", "version": "1.9.0", "version_scheme": "pvp"},
        {"org": "org.policy", "name": "api", "scala_binary": "2.13", "version": "2.1.0", "version_scheme": "pvp"},
        {"org": "org.policy", "name": "helper", "scala_binary": "2.13", "version": "3.0.0", "version_scheme": "always"},
        {"org": "org.policy", "name": "helper", "scala_binary": "2.13", "version": "3.1.0", "version_scheme": "always"},
    ]
    deps = [
        {"from_org": "org.policy", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.policy", "to_name": "api", "to_scala_binary": "2.13", "version": "2.1.0"},
        {"from_org": "org.policy", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.policy", "to_name": "bridge", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "org.policy", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.policy", "to_name": "helper", "to_scala_binary": "2.13", "version": "3.0.0"},
        {"from_org": "org.policy", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.policy", "to_name": "helper", "to_scala_binary": "2.13", "version": "3.1.0"},
    ]
    requests = [
        {"org": "org.policy", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
        {"org": "org.policy", "name": "api", "scala_binary": "2.13", "version": "1.9.0", "scope": "compile", "policy": "force"},
        {"org": "org.policy", "name": "helper", "scala_binary": "2.13", "version": "3.0.0", "scope": "compile", "policy": "deny"},
    ]
    lock, audit = run_case(tmp_path, modules, deps, requests)
    artifacts = by_identity(lock)
    assert artifacts[("org.policy", "api", "2.13", "", "")]["version"] == "1.9.0"
    assert artifacts[("org.policy", "helper", "2.13", "", "")]["version"] == "3.1.0"
    assert any(e["name"] == "api" and e["selected"] == "1.9.0" and e["evicted"] == "2.1.0" and e["compatible"] is False for e in audit["evictions"])
    assert not any(e["name"] == "helper" and e["evicted"] == "3.0.0" for e in audit["evictions"])

    repo = tmp_path / "badrepo"
    repo.mkdir()
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], modules)
    write_tsv(repo / "deps.tsv", ["from_org", "from_name", "from_scala_binary", "from_version", "dep_scope", "to_org", "to_name", "to_scala_binary", "version", "classifier", "platform"], deps)
    request = tmp_path / "bad-request.tsv"
    write_tsv(request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform", "policy"], [
        {"org": "org.policy", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
        {"org": "org.policy", "name": "helper", "scala_binary": "2.13", "version": "3.0.0", "scope": "compile", "policy": "deny"},
        {"org": "org.policy", "name": "helper", "scala_binary": "2.13", "version": "3.1.0", "scope": "compile", "policy": "deny"},
    ])
    lock = tmp_path / "bad-lock.json"
    audit = tmp_path / "bad-audit.json"
    result = run([str(BIN), "--repo", str(repo), "--request", str(request), "--lock", str(lock), "--audit", str(audit)], cwd=str(APP), timeout=90)
    assert_failed_without_outputs(result, lock, audit)


def test_constraints_prune_candidates_before_selection_and_eviction(tmp_path):
    """Repository constraints combine with force policy, ranges, and audit compatibility."""
    modules = [
        {"org": "org.bound", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.bound", "name": "bridge", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "org.bound", "name": "api", "scala_binary": "2.13", "version": "1.5.0", "version_scheme": "pvp"},
        {"org": "org.bound", "name": "api", "scala_binary": "2.13", "version": "1.8.0", "version_scheme": "pvp"},
        {"org": "org.bound", "name": "api", "scala_binary": "2.13", "version": "2.0.0", "version_scheme": "semver-spec"},
    ]
    deps = [
        {"from_org": "org.bound", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.bound", "to_name": "api", "to_scala_binary": "2.13", "version": "1.5.0"},
        {"from_org": "org.bound", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.bound", "to_name": "bridge", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "org.bound", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.bound", "to_name": "api", "to_scala_binary": "2.13", "version": "1.8.0"},
        {"from_org": "org.bound", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "org.bound", "to_name": "api", "to_scala_binary": "2.13", "version": "2.0.0"},
    ]
    constraints = [
        {"org": "org.bound", "name": "api", "scala_binary": "2.13", "min_version": "1.5.0", "max_version": "2.0.0", "include_min": "true", "include_max": "false"},
    ]
    requests = [{"org": "org.bound", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"}]
    lock, audit = run_case(tmp_path, modules, deps, requests, constraints=constraints)
    artifacts = by_identity(lock)
    assert artifacts[("org.bound", "api", "2.13", "", "")]["version"] == "1.8.0"
    assert audit["status"] == "conflict"
    assert any(e["name"] == "api" and e["selected"] == "1.8.0" and e["evicted"] == "1.5.0" and e["scheme"] == "pvp" and e["compatible"] is False for e in audit["evictions"])
    assert not any(e["name"] == "api" and e["evicted"] == "2.0.0" for e in audit["evictions"])

    repo = tmp_path / "forced-outside"
    repo.mkdir()
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], modules)
    write_tsv(repo / "deps.tsv", ["from_org", "from_name", "from_scala_binary", "from_version", "dep_scope", "to_org", "to_name", "to_scala_binary", "version", "classifier", "platform"], deps)
    write_tsv(repo / "constraints.tsv", ["org", "name", "scala_binary", "classifier", "platform", "min_version", "max_version", "include_min", "include_max"], constraints)
    request = tmp_path / "force.tsv"
    write_tsv(request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform", "policy"], [
        {"org": "org.bound", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
        {"org": "org.bound", "name": "api", "scala_binary": "2.13", "version": "2.0.0", "scope": "compile", "policy": "force"},
    ])
    lock = tmp_path / "force-lock.json"
    audit = tmp_path / "force-audit.json"
    result = run([str(BIN), "--repo", str(repo), "--request", str(request), "--lock", str(lock), "--audit", str(audit)], cwd=str(APP), timeout=90)
    assert_failed_without_outputs(result, lock, audit)


def test_exclusions_are_source_specific_and_preserve_variant_reachability(tmp_path):
    """Repository exclusions remove exact outgoing edges without erasing matching roots or variants."""
    modules = [
        {"org": "exclude.app", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "exclude.app", "name": "bridge", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "exclude.lib", "name": "api", "scala_binary": "2.13", "version": "1.1.0", "version_scheme": "semver-spec"},
        {"org": "exclude.lib", "name": "api", "scala_binary": "2.13", "version": "1.2.0", "version_scheme": "semver-spec"},
        {"org": "exclude.native", "name": "jni", "scala_binary": "", "version": "3.0.0", "version_scheme": "always"},
    ]
    deps = [
        {"from_org": "exclude.app", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "exclude.app", "to_name": "bridge", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "exclude.app", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "exclude.lib", "to_name": "api", "to_scala_binary": "2.13", "version": "1.2.0"},
        {"from_org": "exclude.app", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "exclude.lib", "to_name": "api", "to_scala_binary": "2.13", "version": "1.1.0"},
        {"from_org": "exclude.app", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "exclude.native", "to_name": "jni", "to_scala_binary": "", "version": "3.0.0", "platform": "linux-x64"},
        {"from_org": "exclude.app", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "exclude.native", "to_name": "jni", "to_scala_binary": "", "version": "3.0.0", "classifier": "tests", "platform": "linux-x64"},
    ]
    exclusions = [
        {"from_org": "exclude.app", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "to_org": "exclude.lib", "to_name": "api", "to_scala_binary": "2.13", "version": "1.2.0"},
        {"from_org": "exclude.app", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "to_org": "exclude.native", "to_name": "jni", "to_scala_binary": "", "version": "3.0.0", "platform": "linux-x64"},
    ]
    requests = [{"org": "exclude.app", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "runtime"}]
    lock, audit = run_case(tmp_path, modules, deps, requests, exclusions=exclusions)
    artifacts = by_identity(lock)

    assert artifacts[("exclude.lib", "api", "2.13", "", "")]["version"] == "1.1.0"
    assert ("exclude.native", "jni", "", "", "linux-x64") not in artifacts
    assert artifacts[("exclude.native", "jni", "", "tests", "linux-x64")]["version"] == "3.0.0"
    assert audit["evictions"] == []


def test_forced_roots_survive_exclusions_and_expand_independently(tmp_path):
    """A forced component excluded from one root can still enter as a root and carry its own dependencies."""
    modules = [
        {"org": "force.exclude", "name": "app", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "force.exclude", "name": "api", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "semver-spec"},
        {"org": "force.exclude", "name": "api", "scala_binary": "2.13", "version": "2.0.0", "version_scheme": "semver-spec"},
        {"org": "force.exclude", "name": "impl", "scala_binary": "2.13", "version": "5.0.0", "version_scheme": "always"},
    ]
    deps = [
        {"from_org": "force.exclude", "from_name": "app", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "force.exclude", "to_name": "api", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "force.exclude", "from_name": "app", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "force.exclude", "to_name": "api", "to_scala_binary": "2.13", "version": "2.0.0"},
        {"from_org": "force.exclude", "from_name": "api", "from_scala_binary": "2.13", "from_version": "2.0.0", "dep_scope": "runtime", "to_org": "force.exclude", "to_name": "impl", "to_scala_binary": "2.13", "version": "5.0.0"},
    ]
    exclusions = [
        {"from_org": "force.exclude", "from_name": "app", "from_scala_binary": "2.13", "from_version": "1.0.0", "to_org": "force.exclude", "to_name": "api", "to_scala_binary": "2.13", "version": "2.0.0"},
    ]
    requests = [
        {"org": "force.exclude", "name": "app", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
        {"org": "force.exclude", "name": "api", "scala_binary": "2.13", "version": "2.0.0", "scope": "runtime", "policy": "force"},
    ]
    lock, audit = run_case(tmp_path, modules, deps, requests, exclusions=exclusions)
    artifacts = by_identity(lock)

    assert artifacts[("force.exclude", "api", "2.13", "", "")]["version"] == "2.0.0"
    assert artifacts[("force.exclude", "api", "2.13", "", "")]["scope"] == "compile,runtime"
    assert artifacts[("force.exclude", "impl", "2.13", "", "")]["scope"] == "runtime"
    assert any(e["name"] == "api" and e["evicted"] == "1.0.0" and e["compatible"] is False for e in audit["evictions"])


def test_interacting_roots_constraints_overrides_and_variants_remain_canonical(tmp_path):
    """A mixed mirror combines scope domains, constraints, forced selections, denied candidates, and variant identities."""
    modules = [
        {"org": "acme.app", "name": "compile-root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "acme.app", "name": "runtime-root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "acme.app", "name": "runtime-bridge", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "acme.libs", "name": "core", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "pvp"},
        {"org": "acme.libs", "name": "core", "scala_binary": "2.13", "version": "1.1.0", "version_scheme": "pvp"},
        {"org": "acme.libs", "name": "core", "scala_binary": "2.13", "version": "2.0.0", "version_scheme": "semver-spec"},
        {"org": "acme.libs", "name": "util", "scala_binary": "2.13", "version": "0.4.2", "version_scheme": "early-semver"},
        {"org": "acme.libs", "name": "util", "scala_binary": "2.13", "version": "0.4.9", "version_scheme": "early-semver"},
        {"org": "acme.libs", "name": "util", "scala_binary": "2.13", "version": "0.5.0", "version_scheme": "early-semver"},
        {"org": "acme.native", "name": "jni", "scala_binary": "", "version": "3.0.0", "version_scheme": "always"},
        {"org": "acme.native", "name": "jni", "scala_binary": "", "version": "3.1.0", "version_scheme": "always"},
    ]
    deps = [
        {"from_org": "acme.app", "from_name": "compile-root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "acme.libs", "to_name": "core", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "acme.app", "from_name": "compile-root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "acme.libs", "to_name": "util", "to_scala_binary": "2.13", "version": "0.4.9"},
        {"from_org": "acme.app", "from_name": "compile-root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "acme.native", "to_name": "jni", "to_scala_binary": "", "version": "3.0.0", "classifier": "tests", "platform": "linux-x64"},
        {"from_org": "acme.app", "from_name": "runtime-root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "acme.app", "to_name": "runtime-bridge", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "acme.app", "from_name": "runtime-bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "acme.libs", "to_name": "core", "to_scala_binary": "2.13", "version": "1.1.0"},
        {"from_org": "acme.app", "from_name": "runtime-bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "acme.libs", "to_name": "core", "to_scala_binary": "2.13", "version": "2.0.0"},
        {"from_org": "acme.app", "from_name": "runtime-bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "acme.libs", "to_name": "util", "to_scala_binary": "2.13", "version": "0.5.0"},
        {"from_org": "acme.app", "from_name": "runtime-root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "acme.native", "to_name": "jni", "to_scala_binary": "", "version": "3.0.0", "classifier": "", "platform": "linux-x64"},
        {"from_org": "acme.app", "from_name": "runtime-root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "acme.native", "to_name": "jni", "to_scala_binary": "", "version": "3.1.0", "classifier": "", "platform": "linux-x64"},
    ]
    constraints = [
        {"org": "acme.libs", "name": "core", "scala_binary": "2.13", "min_version": "1.0.0", "max_version": "2.0.0", "include_min": "true", "include_max": "false"},
        {"org": "acme.libs", "name": "util", "scala_binary": "2.13", "min_version": "0.4.0", "max_version": "0.5.0", "include_min": "true", "include_max": "false"},
    ]
    requests = [
        {"org": "acme.app", "name": "compile-root", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
        {"org": "acme.app", "name": "runtime-root", "scala_binary": "2.13", "version": "1.0.0", "scope": "runtime"},
        {"org": "acme.libs", "name": "util", "scala_binary": "2.13", "version": "0.4.2", "scope": "compile", "policy": "force"},
        {"org": "acme.native", "name": "jni", "scala_binary": "", "version": "3.0.0", "scope": "runtime", "platform": "linux-x64", "policy": "deny"},
    ]
    lock, audit = run_case(tmp_path, modules, deps, requests, constraints=constraints)
    artifacts = by_identity(lock)

    core = artifacts[("acme.libs", "core", "2.13", "", "")]
    assert core["version"] == "1.1.0"
    assert core["scope"] == "compile,runtime"
    assert artifacts[("acme.libs", "util", "2.13", "", "")]["version"] == "0.4.2"
    assert artifacts[("acme.native", "jni", "", "tests", "linux-x64")]["version"] == "3.0.0"
    assert artifacts[("acme.native", "jni", "", "", "linux-x64")]["version"] == "3.1.0"
    assert ("acme.libs", "core", "2.13", "", "") < ("acme.native", "jni", "", "", "linux-x64")
    assert [(a["org"], a["name"], a["scala_binary"], a["classifier"], a["platform"]) for a in lock["artifacts"]] == sorted(
        (a["org"], a["name"], a["scala_binary"], a["classifier"], a["platform"]) for a in lock["artifacts"]
    )

    assert audit["status"] == "conflict"
    assert any(e["name"] == "core" and e["evicted"] == "1.0.0" and e["compatible"] is False and e["scheme"] == "pvp" for e in audit["evictions"])
    assert not any(e["name"] == "core" and e["evicted"] == "2.0.0" for e in audit["evictions"])
    assert any(e["name"] == "util" and e["selected"] == "0.4.2" and e["evicted"] == "0.4.9" and e["compatible"] is True for e in audit["evictions"])
    assert not any(e["name"] == "util" and e["evicted"] == "0.5.0" for e in audit["evictions"])
    assert not any(e["name"] == "jni" and e["evicted"] == "3.0.0" and e["classifier"] == "" for e in audit["evictions"])


def test_plugin_bootstrap_domain_combines_cycle_termination_with_transitive_selection(tmp_path):
    """Plugin/bootstrap reachability shares one domain while compile dependencies from that domain remain selected."""
    modules = [
        {"org": "build.root", "name": "plug", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "build.tool", "name": "bridge", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "build.tool", "name": "shim", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "build.api", "name": "dsl", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "semver-spec"},
        {"org": "build.api", "name": "dsl", "scala_binary": "2.13", "version": "1.2.0", "version_scheme": "semver-spec"},
    ]
    deps = [
        {"from_org": "build.root", "from_name": "plug", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "plugin", "to_org": "build.tool", "to_name": "bridge", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "build.tool", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "bootstrap", "to_org": "build.tool", "to_name": "shim", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "build.tool", "from_name": "shim", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "plugin", "to_org": "build.tool", "to_name": "bridge", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "build.tool", "from_name": "bridge", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "build.api", "to_name": "dsl", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "build.tool", "from_name": "shim", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "build.api", "to_name": "dsl", "to_scala_binary": "2.13", "version": "1.2.0"},
    ]
    requests = [{"org": "build.root", "name": "plug", "scala_binary": "2.13", "version": "1.0.0", "scope": "bootstrap"}]
    lock, audit = run_case(tmp_path, modules, deps, requests)
    artifacts = by_identity(lock)
    assert artifacts[("build.tool", "bridge", "2.13", "", "")]["scope"] == "bootstrap"
    assert artifacts[("build.tool", "shim", "2.13", "", "")]["scope"] == "bootstrap"
    assert artifacts[("build.api", "dsl", "2.13", "", "")]["version"] == "1.2.0"
    assert artifacts[("build.api", "dsl", "2.13", "", "")]["scope"] == "bootstrap"
    assert audit["status"] == "ok"
    assert any(e["org"] == "build.api" and e["selected"] == "1.2.0" and e["evicted"] == "1.0.0" and e["compatible"] is True for e in audit["evictions"])


def test_invalid_scopes_duplicate_modules_and_conflicting_forces_are_atomic(tmp_path):
    """Invalid metadata classes fail without publishing partial lock or audit files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    duplicate_modules = [
        {"org": "bad", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "bad", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "semver-spec"},
    ]
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], duplicate_modules)
    write_tsv(repo / "deps.tsv", ["from_org", "from_name", "from_scala_binary", "from_version", "dep_scope", "to_org", "to_name", "to_scala_binary", "version", "classifier", "platform"], [])
    request = tmp_path / "request.tsv"
    write_tsv(request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform"], [
        {"org": "bad", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
    ])
    lock = tmp_path / "lock.json"
    audit = tmp_path / "audit.json"
    assert_failed_without_outputs(run([str(BIN), "--repo", str(repo), "--request", str(request), "--lock", str(lock), "--audit", str(audit)], cwd=str(APP), timeout=90), lock, audit)

    repo = tmp_path / "bad-scope"
    repo.mkdir()
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], [
        {"org": "bad", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "bad", "name": "dep", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
    ])
    write_tsv(repo / "deps.tsv", ["from_org", "from_name", "from_scala_binary", "from_version", "dep_scope", "to_org", "to_name", "to_scala_binary", "version", "classifier", "platform"], [
        {"from_org": "bad", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "test", "to_org": "bad", "to_name": "dep", "to_scala_binary": "2.13", "version": "1.0.0"},
    ])
    bad_scope_request = tmp_path / "bad-scope.tsv"
    write_tsv(bad_scope_request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform"], [
        {"org": "bad", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "runtime"},
    ])
    lock = tmp_path / "bad-scope-lock.json"
    audit = tmp_path / "bad-scope-audit.json"
    assert_failed_without_outputs(run([str(BIN), "--repo", str(repo), "--request", str(bad_scope_request), "--lock", str(lock), "--audit", str(audit)], cwd=str(APP), timeout=90), lock, audit)

    modules = [
        {"org": "force", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "force", "name": "api", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "force", "name": "api", "scala_binary": "2.13", "version": "2.0.0", "version_scheme": "always"},
    ]
    deps = [
        {"from_org": "force", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "force", "to_name": "api", "to_scala_binary": "2.13", "version": "1.0.0"},
        {"from_org": "force", "from_name": "root", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "force", "to_name": "api", "to_scala_binary": "2.13", "version": "2.0.0"},
    ]
    repo = tmp_path / "force-repo"
    repo.mkdir()
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], modules)
    write_tsv(repo / "deps.tsv", ["from_org", "from_name", "from_scala_binary", "from_version", "dep_scope", "to_org", "to_name", "to_scala_binary", "version", "classifier", "platform"], deps)
    force_request = tmp_path / "force-request.tsv"
    write_tsv(force_request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform", "policy"], [
        {"org": "force", "name": "root", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
        {"org": "force", "name": "api", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile", "policy": "force"},
        {"org": "force", "name": "api", "scala_binary": "2.13", "version": "2.0.0", "scope": "compile", "policy": "force"},
    ])
    lock = tmp_path / "force-lock.json"
    audit = tmp_path / "force-audit.json"
    assert_failed_without_outputs(run([str(BIN), "--repo", str(repo), "--request", str(force_request), "--lock", str(lock), "--audit", str(audit)], cwd=str(APP), timeout=90), lock, audit)


def test_force_only_roots_still_evict_reached_versions(tmp_path):
    """Forced request rows are roots and can override versions reached from other roots."""
    modules = [
        {"org": "force.root", "name": "app", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "force.lib", "name": "api", "scala_binary": "2.13", "version": "1.4.0", "version_scheme": "semver-spec"},
        {"org": "force.lib", "name": "api", "scala_binary": "2.13", "version": "2.0.0", "version_scheme": "semver-spec"},
        {"org": "force.lib", "name": "adapter", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
    ]
    deps = [
        {"from_org": "force.root", "from_name": "app", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "force.lib", "to_name": "api", "to_scala_binary": "2.13", "version": "1.4.0"},
        {"from_org": "force.lib", "from_name": "api", "from_scala_binary": "2.13", "from_version": "2.0.0", "dep_scope": "compile", "to_org": "force.lib", "to_name": "adapter", "to_scala_binary": "2.13", "version": "1.0.0"},
    ]
    requests = [
        {"org": "force.root", "name": "app", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
        {"org": "force.lib", "name": "api", "scala_binary": "2.13", "version": "2.0.0", "scope": "runtime", "policy": "force"},
    ]
    lock, audit = run_case(tmp_path, modules, deps, requests)
    artifacts = by_identity(lock)

    assert artifacts[("force.lib", "api", "2.13", "", "")]["version"] == "2.0.0"
    assert artifacts[("force.lib", "api", "2.13", "", "")]["scope"] == "compile,runtime"
    assert artifacts[("force.lib", "adapter", "2.13", "", "")]["scope"] == "runtime"
    assert audit["status"] == "conflict"
    assert any(
        e["org"] == "force.lib"
        and e["name"] == "api"
        and e["selected"] == "2.0.0"
        and e["evicted"] == "1.4.0"
        and e["compatible"] is False
        for e in audit["evictions"]
    )


def test_variant_constraints_and_suffix_versions_stay_identity_specific(tmp_path):
    """Classifier/platform constraints and suffix version ordering do not bleed across identities."""
    modules = [
        {"org": "variant.root", "name": "app", "scala_binary": "", "version": "1.0.0", "version_scheme": "always"},
        {"org": "variant.native", "name": "jni", "scala_binary": "", "version": "1.0.0-alpha", "version_scheme": "semver-spec"},
        {"org": "variant.native", "name": "jni", "scala_binary": "", "version": "1.0.0-beta", "version_scheme": "semver-spec"},
        {"org": "variant.native", "name": "jni", "scala_binary": "", "version": "2.0.0", "version_scheme": "semver-spec"},
    ]
    deps = [
        {"from_org": "variant.root", "from_name": "app", "from_scala_binary": "", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "variant.native", "to_name": "jni", "to_scala_binary": "", "version": "1.0.0-alpha", "classifier": "tests", "platform": "linux-x64"},
        {"from_org": "variant.root", "from_name": "app", "from_scala_binary": "", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "variant.native", "to_name": "jni", "to_scala_binary": "", "version": "1.0.0-beta", "classifier": "tests", "platform": "linux-x64"},
        {"from_org": "variant.root", "from_name": "app", "from_scala_binary": "", "from_version": "1.0.0", "dep_scope": "runtime", "to_org": "variant.native", "to_name": "jni", "to_scala_binary": "", "version": "2.0.0", "classifier": "", "platform": "linux-x64"},
    ]
    constraints = [
        {
            "org": "variant.native",
            "name": "jni",
            "classifier": "tests",
            "platform": "linux-x64",
            "min_version": "1.0.0-alpha",
            "max_version": "2.0.0",
            "include_min": "true",
            "include_max": "false",
        },
    ]
    requests = [{"org": "variant.root", "name": "app", "version": "1.0.0", "scope": "runtime"}]
    lock, audit = run_case(tmp_path, modules, deps, requests, constraints=constraints)
    artifacts = by_identity(lock)

    assert artifacts[("variant.native", "jni", "", "tests", "linux-x64")]["version"] == "1.0.0-beta"
    assert artifacts[("variant.native", "jni", "", "", "linux-x64")]["version"] == "2.0.0"
    assert audit["status"] == "ok"
    assert any(
        e["classifier"] == "tests"
        and e["selected"] == "1.0.0-beta"
        and e["evicted"] == "1.0.0-alpha"
        and e["compatible"] is True
        for e in audit["evictions"]
    )
    assert not any(e["selected"] == "2.0.0" and e["evicted"] == "1.0.0-beta" for e in audit["evictions"])


def test_alias_canonicalization_crosscuts_constraints_exclusions_and_outputs(tmp_path):
    """Repository aliases canonicalize every planning input before reachability, policy, and output decisions."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_aliases(repo, [
        {"alias_org": "legacy.root", "alias_name": "app_2.13", "alias_scala_binary": "", "org": "canon.root", "name": "app", "scala_binary": "2.13"},
        {"alias_org": "legacy.lib", "alias_name": "api_2.13", "alias_scala_binary": "", "org": "canon.lib", "name": "api", "scala_binary": "2.13"},
        {"alias_org": "legacy.lib", "alias_name": "helper_2.13", "alias_scala_binary": "", "org": "canon.lib", "name": "helper", "scala_binary": "2.13"},
    ])
    modules = [
        {"org": "canon.root", "name": "app", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
        {"org": "canon.lib", "name": "api", "scala_binary": "2.13", "version": "1.4.0", "version_scheme": "pvp"},
        {"org": "canon.lib", "name": "api", "scala_binary": "2.13", "version": "1.5.0", "version_scheme": "pvp"},
        {"org": "canon.lib", "name": "api", "scala_binary": "2.13", "version": "2.0.0", "version_scheme": "pvp"},
        {"org": "canon.lib", "name": "helper", "scala_binary": "2.13", "version": "3.0.0", "version_scheme": "always"},
    ]
    deps = [
        {"from_org": "legacy.root", "from_name": "app_2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "legacy.lib", "to_name": "api_2.13", "version": "1.4.0"},
        {"from_org": "canon.root", "from_name": "app", "from_scala_binary": "2.13", "from_version": "1.0.0", "dep_scope": "compile", "to_org": "canon.lib", "to_name": "api", "to_scala_binary": "2.13", "version": "2.0.0"},
        {"from_org": "canon.lib", "from_name": "api", "from_scala_binary": "2.13", "from_version": "1.5.0", "dep_scope": "compile", "to_org": "legacy.lib", "to_name": "helper_2.13", "version": "3.0.0", "classifier": "tests", "platform": "linux-x64"},
    ]
    constraints = [
        {"org": "legacy.lib", "name": "api_2.13", "min_version": "1.4.0", "max_version": "2.0.0", "include_min": "true", "include_max": "false"},
    ]
    exclusions = [
        {"from_org": "legacy.root", "from_name": "app_2.13", "from_version": "1.0.0", "to_org": "canon.lib", "to_name": "api", "to_scala_binary": "2.13", "version": "2.0.0"},
    ]
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], modules)
    write_tsv(repo / "deps.tsv", ["from_org", "from_name", "from_scala_binary", "from_version", "dep_scope", "to_org", "to_name", "to_scala_binary", "version", "classifier", "platform"], deps)
    write_tsv(repo / "constraints.tsv", ["org", "name", "scala_binary", "classifier", "platform", "min_version", "max_version", "include_min", "include_max"], constraints)
    write_tsv(repo / "exclusions.tsv", ["from_org", "from_name", "from_scala_binary", "from_version", "to_org", "to_name", "to_scala_binary", "version", "classifier", "platform"], exclusions)
    request = tmp_path / "request.tsv"
    write_tsv(request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform", "policy"], [
        {"org": "legacy.root", "name": "app_2.13", "version": "1.0.0", "scope": "compile"},
        {"org": "legacy.lib", "name": "api_2.13", "version": "1.5.0", "scope": "runtime", "policy": "force"},
    ])
    lock = tmp_path / "lock.json"
    audit = tmp_path / "audit.json"
    result = run([str(BIN), "--repo", str(repo), "--request", str(request), "--lock", str(lock), "--audit", str(audit)], cwd=str(APP), timeout=90)
    assert result.returncode == 0, result.stderr + result.stdout
    artifacts = by_identity(json.loads(lock.read_text()))
    assert ("legacy.lib", "api_2.13", "", "", "") not in artifacts
    assert artifacts[("canon.lib", "api", "2.13", "", "")]["version"] == "1.5.0"
    assert artifacts[("canon.lib", "api", "2.13", "", "")]["scope"] == "compile,runtime"
    assert artifacts[("canon.lib", "helper", "2.13", "tests", "linux-x64")]["scope"] == "runtime"
    evictions = json.loads(audit.read_text())["evictions"]
    assert any(e["org"] == "canon.lib" and e["name"] == "api" and e["selected"] == "1.5.0" and e["evicted"] == "1.4.0" for e in evictions)
    assert not any(e["evicted"] == "2.0.0" for e in evictions)


def test_alias_conflicts_and_cycles_are_invalid_before_outputs(tmp_path):
    """Alias metadata conflicts are invalid and cannot publish complete planning artifacts."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_tsv(repo / "aliases.tsv", ["alias_org", "alias_name", "alias_scala_binary", "org", "name", "scala_binary"], [
        {"alias_org": "cycle", "alias_name": "a", "alias_scala_binary": "2.13", "org": "cycle", "name": "b", "scala_binary": "2.13"},
        {"alias_org": "cycle", "alias_name": "b", "alias_scala_binary": "2.13", "org": "cycle", "name": "a", "scala_binary": "2.13"},
    ])
    write_tsv(repo / "modules.tsv", ["org", "name", "scala_binary", "version", "version_scheme"], [
        {"org": "cycle", "name": "a", "scala_binary": "2.13", "version": "1.0.0", "version_scheme": "always"},
    ])
    request = tmp_path / "request.tsv"
    write_tsv(request, ["org", "name", "scala_binary", "version", "scope", "classifier", "platform"], [
        {"org": "cycle", "name": "a", "scala_binary": "2.13", "version": "1.0.0", "scope": "compile"},
    ])
    lock = tmp_path / "lock.json"
    audit = tmp_path / "audit.json"
    assert_failed_without_outputs(run([str(BIN), "--repo", str(repo), "--request", str(request), "--lock", str(lock), "--audit", str(audit)], cwd=str(APP), timeout=90), lock, audit)
