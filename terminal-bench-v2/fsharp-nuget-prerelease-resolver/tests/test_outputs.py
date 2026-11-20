import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

APP = Path("/app/nugate")
AGENT_UID = 999
AGENT_GID = 999


@pytest.fixture(scope="session", autouse=True)
def built_tool():
    """Build the F# admission tool once for the verifier session."""
    agent = __import__("pwd").getpwnam("agent")
    group = __import__("grp").getgrnam("agent")
    global AGENT_UID, AGENT_GID
    AGENT_UID, AGENT_GID = agent.pw_uid, group.gr_gid
    build_script = APP / "bin" / "build.sh"
    script_stat = build_script.stat()
    assert script_stat.st_uid == 0
    assert script_stat.st_gid == 0
    assert stat.S_IMODE(script_stat.st_mode) == 0o555
    tests_stat = Path("/tests").stat()
    assert stat.S_IMODE(tests_stat.st_mode) & 0o077 == 0
    subprocess.run(
        [str(build_script)],
        check=True,
        cwd=APP,
        user=AGENT_UID,
        group=AGENT_GID,
        extra_groups=[],
    )
    tool = APP / "bin" / "nugate"
    tool_stat = tool.stat()
    assert tool_stat.st_uid == AGENT_UID
    assert tool_stat.st_gid == AGENT_GID
    assert tool.is_file()
    assert tool.read_bytes()[:4] == b"\x7fELF"


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def package(repo: Path, rel: str, package_id: str, version: str, frameworks, listed=True, aliases=None):
    data = {"id": package_id, "version": version, "listed": listed, "frameworks": frameworks}
    if aliases is not None:
        data["aliases"] = aliases
    write_json(repo / rel, data)


def repo_hash(repo: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in repo.rglob("*.json") if p.is_file()):
        rel = path.relative_to(repo).as_posix().encode()
        h.update(rel)
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def snapshot(repo: Path):
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob("*") if p.is_file()}


def grant_agent_io(tmp_path: Path, repo: Path, request_path: Path):
    for parent in [tmp_path.parent, tmp_path.parent.parent]:
        parent.chmod(0o755)
    os.chown(tmp_path, AGENT_UID, AGENT_GID)
    tmp_path.chmod(0o755)
    request_path.chmod(0o644)
    for path in repo.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o644)


def run_nugate(tmp_path: Path, repo: Path, request, expect_success: bool):
    request_path = tmp_path / "request.json"
    lock_path = tmp_path / "lock.json"
    audit_path = tmp_path / "audit.json"
    write_json(request_path, request)
    grant_agent_io(tmp_path, repo, request_path)
    before = snapshot(repo)
    proc = subprocess.run(
        [
            str(APP / "bin" / "nugate"),
            "--repo",
            str(repo),
            "--request",
            str(request_path),
            "--lock",
            str(lock_path),
            "--audit",
            str(audit_path),
        ],
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
        user=AGENT_UID,
        group=AGENT_GID,
        extra_groups=[],
    )
    assert snapshot(repo) == before, proc.stderr
    assert lock_path.is_file(), proc.stderr
    assert audit_path.is_file(), proc.stderr
    if expect_success:
        assert proc.returncode == 0, proc.stderr
    else:
        assert proc.returncode != 0
    return json.loads(lock_path.read_text()), json.loads(audit_path.read_text()), lock_path.read_text(), audit_path.read_text()


def test_accepts_full_closure_with_prerelease_semver_and_nearest_framework(tmp_path):
    """Accepted plans resolve transitive ranges, prerelease ordering, and target-framework assets together."""
    repo = tmp_path / "mirror"
    package(repo, "z/acme-app-1.0.0.json", "Acme.App", "1.0.0", {
        "netstandard2.0": [{"id": "Legacy", "range": "[9.0.0,10.0.0)"}],
        "net8.0": [{"id": "LibX", "range": "[2.0.0-beta.2,2.0.0)"}, {"id": "Compat", "range": "[1.0.0,2.0.0)"}],
    })
    package(repo, "b/libx-2.0.0-beta.1.json", "LibX", "2.0.0-beta.1", {"netstandard2.1": []})
    package(repo, "a/libx-2.0.0-beta.10.json", "LibX", "2.0.0-beta.10", {"netstandard2.1": []})
    package(repo, "c/libx-2.0.0.json", "LibX", "2.0.0", {"netstandard2.1": []})
    package(repo, "compat/compat-1.5.0.json", "Compat", "1.5.0", {"netstandard2.0": [{"id": "Core", "range": "[3.0.0,4.0.0)"}]})
    package(repo, "core/core-3.2.0.json", "Core", "3.2.0", {"net6.0": []})
    package(repo, "core/core-3.5.0.json", "Core", "3.5.0", {"net9.0": []})
    request = {"targetFramework": "net8.0", "packages": [{"id": "Acme.App", "range": "[1.0.0,2.0.0)"}]}

    lock, audit, lock_text, audit_text = run_nugate(tmp_path, repo, request, expect_success=True)
    assert lock["accepted"] is True
    assert lock["targetFramework"] == request["targetFramework"]
    assert [(p["id"], p["version"], p["framework"], p["requested"]) for p in lock["packages"]] == [
        ("Acme.App", "1.0.0", "net8.0", True),
        ("Compat", "1.5.0", "netstandard2.0", False),
        ("Core", "3.2.0", "net6.0", False),
        ("LibX", "2.0.0-beta.10", "netstandard2.1", False),
    ]
    deps = {p["id"]: p["dependencies"] for p in lock["packages"]}
    assert deps["Acme.App"] == [{"id": "Compat", "range": "[1.0.0,2.0.0)"}, {"id": "LibX", "range": "[2.0.0-beta.2,2.0.0)"}]
    assert deps["Compat"] == [{"id": "Core", "range": "[3.0.0,4.0.0)"}]
    assert audit["decision"] == "accepted"
    assert audit["targetFramework"] == "net8.0"
    assert audit["roots"] == ["Acme.App"]
    assert audit["repositoryHash"] == repo_hash(repo)
    second = run_nugate(tmp_path / "again", repo, request, expect_success=True)
    assert second[2] == lock_text
    assert second[3] == audit_text


def test_new_transitive_ranges_can_lower_an_existing_selection(tmp_path):
    """The resolver revisits earlier package choices after later dependency ranges are discovered."""
    repo = tmp_path / "mirror"
    package(repo, "x/x-2.5.0.json", "Shared.X", "2.5.0", {"net8.0": []})
    package(repo, "x/x-1.5.0.json", "Shared.X", "1.5.0", {"net8.0": []})
    package(repo, "a/a-1.0.0.json", "Feature.A", "1.0.0", {"net8.0": [{"id": "Shared.X", "range": "[1.0.0,2.0.0)"}]})
    request = {"targetFramework": "net8.0", "packages": [
        {"id": "Shared.X", "range": "[1.0.0,3.0.0)"},
        {"id": "Feature.A", "range": "[1.0.0,2.0.0)"},
    ]}

    lock, _, _, _ = run_nugate(tmp_path, repo, request, expect_success=True)
    assert [(p["id"], p["version"], p["requested"]) for p in lock["packages"]] == [
        ("Feature.A", "1.0.0", True),
        ("Shared.X", "1.5.0", True),
    ]


def test_non_exact_framework_rank_prefers_highest_net_before_netstandard(tmp_path):
    """A compatible netN.0 asset outranks netstandard fallback when no exact group exists."""
    repo = tmp_path / "mirror"
    package(repo, "host/host-1.0.0.json", "Framework.Host", "1.0.0", {
        "netstandard2.1": [{"id": "Wrong.Asset", "range": "1.0.0"}],
        "net6.0": [{"id": "Older.Asset", "range": "1.0.0"}],
        "net7.0": [{"id": "Chosen.Asset", "range": "1.0.0"}],
    })
    package(repo, "chosen/chosen-1.0.0.json", "Chosen.Asset", "1.0.0", {"net8.0": []})
    package(repo, "older/older-1.0.0.json", "Older.Asset", "1.0.0", {"net8.0": []})
    package(repo, "wrong/wrong-1.0.0.json", "Wrong.Asset", "1.0.0", {"net8.0": []})
    request = {"targetFramework": "net8.0", "packages": [{"id": "Framework.Host", "range": "[1.0.0,2.0.0)"}]}

    lock, _, _, _ = run_nugate(tmp_path, repo, request, expect_success=True)
    host = next(p for p in lock["packages"] if p["id"] == "Framework.Host")
    assert host["framework"] == "net7.0"
    assert host["dependencies"] == [{"id": "Chosen.Asset", "range": "1.0.0"}]
    assert [p["id"] for p in lock["packages"]] == ["Chosen.Asset", "Framework.Host"]


def test_rejects_when_only_unlisted_prerelease_or_incompatible_assets_match(tmp_path):
    """Rejected plans ignore unlisted packages, default-stable ranges, and incompatible framework groups."""
    repo = tmp_path / "mirror"
    package(repo, "alpha/pre.json", "StableOnly", "1.5.0-alpha.1", {"net8.0": []})
    package(repo, "unlisted/stable.json", "StableOnly", "1.5.0", {"net8.0": []}, listed=False)
    package(repo, "future/stable.json", "StableOnly", "1.4.0", {"net9.0": []})
    request = {"targetFramework": "net8.0", "packages": [{"id": "StableOnly", "range": "[1.0.0,2.0.0)"}]}

    lock, audit, _, _ = run_nugate(tmp_path, repo, request, expect_success=False)
    assert lock["accepted"] is False
    assert lock["targetFramework"] == request["targetFramework"]
    assert lock["packages"] == []
    assert lock["rejected"][0]["id"] == "StableOnly"
    assert lock["rejected"][0]["ranges"] == ["[1.0.0,2.0.0)"]
    assert "no listed compatible version" in lock["rejected"][0]["reason"]
    assert audit["decision"] == "rejected"
    assert audit["repositoryHash"] == repo_hash(repo)


def test_package_without_listed_field_defaults_to_listed(tmp_path):
    """Package metadata that omits listed is treated as listed and remains selectable."""
    repo = tmp_path / "mirror"
    write_json(repo / "implicit/listed-default.json", {
        "id": "Implicit.Listed",
        "version": "1.0.0",
        "frameworks": {"net8.0": []},
    })
    request = {"targetFramework": "net8.0", "packages": [{"id": "implicit.listed", "range": "[1.0.0,2.0.0)"}]}

    lock, audit, _, _ = run_nugate(tmp_path, repo, request, expect_success=True)
    assert lock["accepted"] is True
    assert lock["rejected"] == []
    assert lock["packages"] == [{
        "id": "Implicit.Listed",
        "version": "1.0.0",
        "requested": True,
        "framework": "net8.0",
        "dependencies": [],
    }]
    assert audit["decision"] == "accepted"


def test_rejects_missing_transitive_dependency_with_audit_outputs(tmp_path):
    """A package whose selected framework depends on an absent package cannot be admitted."""
    repo = tmp_path / "mirror"
    package(repo, "root/root-1.0.0.json", "Root", "1.0.0", {"net8.0": [{"id": "Absent", "range": "[4.0.0,5.0.0)"}]})
    request = {"targetFramework": "net8.0", "packages": [{"id": "Root", "range": "[1.0.0,2.0.0)"}]}

    lock, audit, _, _ = run_nugate(tmp_path, repo, request, expect_success=False)
    assert lock["accepted"] is False
    assert lock["targetFramework"] == request["targetFramework"]
    assert len(lock["rejected"]) == 1
    rejected = lock["rejected"][0]
    assert rejected["id"] == "Absent"
    assert rejected["ranges"] == ["[4.0.0,5.0.0)"]
    assert "no listed compatible version" in rejected["reason"]
    assert "target net8.0" in rejected["reason"]
    assert "[4.0.0,5.0.0)" in rejected["reason"]
    assert audit["decision"] == "rejected"
    assert any(event["id"] == "Absent" and event["action"] == "reject" for event in audit["events"])


def test_rejection_audit_places_sorted_selects_before_lock_ordered_rejects(tmp_path):
    """Rejected audits list selected packages first, then rejection events in lock order."""
    repo = tmp_path / "mirror"
    package(repo, "root/root-1.0.0.json", "Root.App", "1.0.0", {
        "net8.0": [
            {"id": "Zeta.Missing", "range": "[2.0.0,3.0.0)"},
            {"id": "Alpha.Missing", "range": "[1.0.0,2.0.0)"},
        ]
    })
    package(repo, "side/side-1.0.0.json", "Side.Lib", "1.0.0", {"net8.0": []})
    request = {"targetFramework": "net8.0", "packages": [
        {"id": "side.lib", "range": "[1.0.0,2.0.0)"},
        {"id": "root.app", "range": "[1.0.0,2.0.0)"},
    ]}

    lock, audit, _, _ = run_nugate(tmp_path, repo, request, expect_success=False)
    assert lock["accepted"] is False
    assert [item["id"] for item in lock["rejected"]] == ["Alpha.Missing", "Zeta.Missing"]
    assert len(lock["rejected"]) == 2
    expected_events = [
        {"action": "select", "id": "Root.App", "version": "1.0.0", "detail": "net8.0"},
        {"action": "select", "id": "Side.Lib", "version": "1.0.0", "detail": "net8.0"},
    ] + [
        {"action": "reject", "id": item["id"], "version": "", "detail": item["reason"]}
        for item in lock["rejected"]
    ]
    assert audit["events"] == expected_events


def test_case_insensitive_ids_exact_ranges_unbounded_ranges_and_select_audit(tmp_path):
    """Requests may use different casing while selected package ids and audit events use metadata casing."""
    repo = tmp_path / "mirror"
    package(repo, "case/case-1.0.0.json", "Case.Lib", "1.0.0", {
        "net8.0": [
            {"id": "exact.util", "range": "1.2.3"},
            {"id": "Range.Upper", "range": "(,2.0.0]"},
            {"id": "Range.Lower", "range": "[3.0.0,)"},
        ]
    })
    package(repo, "exact/exact-1.2.2.json", "Exact.Util", "1.2.2", {"net8.0": []})
    package(repo, "exact/exact-1.2.3.json", "Exact.Util", "1.2.3", {"net8.0": []})
    package(repo, "exact/exact-1.2.4.json", "Exact.Util", "1.2.4", {"net8.0": []})
    package(repo, "upper/upper-2.0.0.json", "Range.Upper", "2.0.0", {"net8.0": []})
    package(repo, "upper/upper-2.1.0.json", "Range.Upper", "2.1.0", {"net8.0": []})
    package(repo, "lower/lower-3.0.0.json", "Range.Lower", "3.0.0", {"net8.0": []})
    package(repo, "lower/lower-3.4.0.json", "Range.Lower", "3.4.0", {"net8.0": []})
    request = {"targetFramework": "net8.0", "packages": [{"id": "case.lib", "range": "[1.0.0,2.0.0)"}]}

    lock, audit, _, _ = run_nugate(tmp_path, repo, request, expect_success=True)
    assert [(p["id"], p["version"], p["requested"]) for p in lock["packages"]] == [
        ("Case.Lib", "1.0.0", True),
        ("Exact.Util", "1.2.3", False),
        ("Range.Lower", "3.4.0", False),
        ("Range.Upper", "2.0.0", False),
    ]
    root = next(p for p in lock["packages"] if p["id"] == "Case.Lib")
    assert root["dependencies"] == [
        {"id": "exact.util", "range": "1.2.3"},
        {"id": "Range.Lower", "range": "[3.0.0,)"},
        {"id": "Range.Upper", "range": "(,2.0.0]"},
    ]
    select_events = {(event["id"], event["version"], event["detail"]) for event in audit["events"] if event["action"] == "select"}
    assert select_events == {
        ("Case.Lib", "1.0.0", "net8.0"),
        ("Exact.Util", "1.2.3", "net8.0"),
        ("Range.Lower", "3.4.0", "net8.0"),
        ("Range.Upper", "2.0.0", "net8.0"),
    }


def test_prerelease_numeric_identifiers_sort_before_nonnumeric_identifiers(tmp_path):
    """Prerelease precedence treats numeric identifiers as lower than nonnumeric identifiers."""
    repo = tmp_path / "mirror"
    package(repo, "tool/alpha-10.json", "Pre.Tool", "1.0.0-alpha.10", {"net8.0": []})
    package(repo, "tool/alpha-beta.json", "Pre.Tool", "1.0.0-alpha.beta", {"net8.0": []})
    package(repo, "tool/beta.json", "Pre.Tool", "1.0.0-beta", {"net8.0": []})
    package(repo, "tool/release.json", "Pre.Tool", "1.0.0", {"net8.0": []})
    request = {"targetFramework": "net8.0", "packages": [{"id": "pre.tool", "range": "[1.0.0-alpha.1,1.0.0-alpha.beta]"}]}

    lock, audit, _, _ = run_nugate(tmp_path, repo, request, expect_success=True)
    assert lock["packages"] == [{
        "id": "Pre.Tool",
        "version": "1.0.0-alpha.beta",
        "requested": True,
        "framework": "net8.0",
        "dependencies": [],
    }]
    assert audit["events"] == [{"action": "select", "id": "Pre.Tool", "version": "1.0.0-alpha.beta", "detail": "net8.0"}]


def test_alias_ranges_coalesce_with_cyclic_transitive_constraints(tmp_path):
    """Alias and canonical references resolve as one package across cyclic transitive dependencies."""
    repo = tmp_path / "mirror"
    package(repo, "gateway/gateway-1.0.0.json", "Gateway", "1.0.0", {
        "net8.0": [
            {"id": "old.core", "range": "[2.0.0,3.0.0)"},
            {"id": "Telemetry.Alias", "range": "[1.0.0,2.0.0)"},
        ]
    })
    package(repo, "core/core-2.7.0.json", "Core.Engine", "2.7.0", {
        "net8.0": [{"id": "gateway.core", "range": "[2.4.0,2.6.0]"}]
    }, aliases=["old.core", "gateway.core"])
    package(repo, "core/core-2.5.0.json", "Core.Engine", "2.5.0", {
        "net8.0": [{"id": "Telemetry", "range": "[1.5.0,2.0.0)"}]
    }, aliases=["old.core", "gateway.core"])
    package(repo, "telemetry/telemetry-1.4.0.json", "Telemetry", "1.4.0", {"net8.0": []}, aliases=["Telemetry.Alias"])
    package(repo, "telemetry/telemetry-1.6.0.json", "Telemetry", "1.6.0", {"net8.0": []}, aliases=["Telemetry.Alias"])
    request = {"targetFramework": "net8.0", "packages": [
        {"id": "Gateway", "range": "[1.0.0,2.0.0)"},
        {"id": "gateway.core", "range": "[2.0.0,3.0.0)"},
    ]}

    lock, audit, _, _ = run_nugate(tmp_path, repo, request, expect_success=True)
    assert [(p["id"], p["version"], p["requested"]) for p in lock["packages"]] == [
        ("Core.Engine", "2.5.0", True),
        ("Gateway", "1.0.0", True),
        ("Telemetry", "1.6.0", False),
    ]
    core = next(p for p in lock["packages"] if p["id"] == "Core.Engine")
    assert core["dependencies"] == [{"id": "Telemetry", "range": "[1.5.0,2.0.0)"}]
    assert audit["roots"] == ["Gateway", "gateway.core"]
    assert [event["id"] for event in audit["events"] if event["action"] == "select"] == [
        "Core.Engine",
        "Gateway",
        "Telemetry",
    ]


def test_rejects_canonical_alias_package_and_unmatched_dependency_together(tmp_path):
    """Rejected plans report both canonical alias failures and unmatched dependency ids from one fixed point."""
    repo = tmp_path / "mirror"
    package(repo, "root/root-1.0.0.json", "Root", "1.0.0", {
        "net8.0": [
            {"id": "cache.alias", "range": "[4.0.0,5.0.0)"},
            {"id": "Ghost.Lib", "range": "[1.0.0,2.0.0)"},
        ]
    })
    package(repo, "cache/cache-3.5.0.json", "Cache.Core", "3.5.0", {"net8.0": []}, aliases=["cache.alias"])
    request = {"targetFramework": "net8.0", "packages": [{"id": "Root", "range": "[1.0.0,2.0.0)"}]}

    lock, audit, _, _ = run_nugate(tmp_path, repo, request, expect_success=False)
    assert lock["accepted"] is False
    assert [(item["id"], item["ranges"]) for item in lock["rejected"]] == [
        ("Cache.Core", ["[4.0.0,5.0.0)"]),
        ("Ghost.Lib", ["[1.0.0,2.0.0)"]),
    ]
    assert all("target net8.0" in item["reason"] for item in lock["rejected"])
    assert [event["action"] for event in audit["events"]] == ["select", "reject", "reject"]
    assert audit["events"][0] == {"action": "select", "id": "Root", "version": "1.0.0", "detail": "net8.0"}


def test_duplicate_alias_roots_constrain_one_requested_package_with_original_roots(tmp_path):
    """Duplicate root aliases coalesce into one requested package while audit roots keep request order and casing."""
    repo = tmp_path / "mirror"
    package(repo, "engine/engine-2.8.0.json", "Runtime.Engine", "2.8.0", {
        "net8.0": [{"id": "Plugin.Host", "range": "[4.0.0,5.0.0)"}]
    }, aliases=["engine.old", "Engine.Legacy"])
    package(repo, "engine/engine-2.2.0.json", "Runtime.Engine", "2.2.0", {
        "net8.0": [{"id": "Plugin.Host", "range": "[3.0.0,4.0.0)"}]
    }, aliases=["engine.old", "Engine.Legacy"])
    package(repo, "plugin/plugin-4.5.0.json", "Plugin.Host", "4.5.0", {"net8.0": []})
    package(repo, "plugin/plugin-3.9.0.json", "Plugin.Host", "3.9.0", {"net8.0": []})
    request = {"targetFramework": "net8.0", "packages": [
        {"id": "Engine.Legacy", "range": "[2.0.0,3.0.0)"},
        {"id": "runtime.engine", "range": "(,3.0.0)"},
        {"id": "engine.old", "range": "[2.5.0,)"},
    ]}

    lock, audit, _, _ = run_nugate(tmp_path, repo, request, expect_success=True)
    assert audit["roots"] == ["Engine.Legacy", "runtime.engine", "engine.old"]
    assert lock["packages"] == [
        {
            "id": "Plugin.Host",
            "version": "4.5.0",
            "requested": False,
            "framework": "net8.0",
            "dependencies": [],
        },
        {
            "id": "Runtime.Engine",
            "version": "2.8.0",
            "requested": True,
            "framework": "net8.0",
            "dependencies": [{"id": "Plugin.Host", "range": "[4.0.0,5.0.0)"}],
        },
    ]


def test_rejection_keeps_fixed_point_selections_and_all_active_alias_ranges(tmp_path):
    """Rejected plans still audit converged selections and report all ranges for canonical alias failures."""
    repo = tmp_path / "mirror"
    package(repo, "suite/suite-1.0.0.json", "Suite.Root", "1.0.0", {
        "net8.0": [
            {"id": "parser.old", "range": "[1.5.0,2.0.0)"},
            {"id": "Missing.Tool", "range": "[7.0.0,8.0.0)"},
        ]
    })
    package(repo, "parser/parser-1.4.0.json", "Parser.Core", "1.4.0", {"net8.0": []}, aliases=["parser.old"])
    package(repo, "parser/parser-2.1.0.json", "Parser.Core", "2.1.0", {"net8.0": []}, aliases=["parser.old"])
    package(repo, "logger/logger-3.0.0.json", "Logger", "3.0.0", {"net8.0": []})
    request = {"targetFramework": "net8.0", "packages": [
        {"id": "Suite.Root", "range": "[1.0.0,2.0.0)"},
        {"id": "LOGGER", "range": "[3.0.0,4.0.0)"},
        {"id": "Parser.Core", "range": "[1.0.0,3.0.0)"},
    ]}

    lock, audit, _, _ = run_nugate(tmp_path, repo, request, expect_success=False)
    assert lock["accepted"] is False
    assert lock["packages"] == []
    assert [(item["id"], item["ranges"]) for item in lock["rejected"]] == [
        ("Missing.Tool", ["[7.0.0,8.0.0)"]),
        ("Parser.Core", ["[1.0.0,3.0.0)", "[1.5.0,2.0.0)"]),
    ]
    parser_reason = lock["rejected"][1]["reason"]
    assert "[1.0.0,3.0.0)" in parser_reason
    assert "[1.5.0,2.0.0)" in parser_reason
    assert [event["action"] for event in audit["events"]] == [
        "select", "select", "select", "reject", "reject"
    ]
    assert audit["events"][:3] == [
        {"action": "select", "id": "Logger", "version": "3.0.0", "detail": "net8.0"},
        {"action": "select", "id": "Parser.Core", "version": "2.1.0", "detail": "net8.0"},
        {"action": "select", "id": "Suite.Root", "version": "1.0.0", "detail": "net8.0"},
    ]
