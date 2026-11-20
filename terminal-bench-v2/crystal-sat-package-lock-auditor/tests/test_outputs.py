import itertools
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

APP = Path("/app/plock")


def split_version(value):
    main, sep, pre = value.partition("-")
    nums = [int(part) if part.isdigit() else 0 for part in main.split(".")]
    nums = (nums + [0, 0, 0])[:3]
    pre_parts = pre.split(".") if sep else []
    return nums, pre_parts


def cmp_ident(a, b):
    ad = a.isdigit()
    bd = b.isdigit()
    if ad and bd:
        return (int(a) > int(b)) - (int(a) < int(b))
    if ad != bd:
        return -1 if ad else 1
    return (a > b) - (a < b)


def cmp_version(a, b):
    an, ap = split_version(a)
    bn, bp = split_version(b)
    for left, right in zip(an, bn):
        if left != right:
            return (left > right) - (left < right)
    if not ap and not bp:
        return 0
    if not ap:
        return 1
    if not bp:
        return -1
    for left, right in itertools.zip_longest(ap, bp, fillvalue=None):
        if left is None:
            return -1
        if right is None:
            return 1
        c = cmp_ident(left, right)
        if c:
            return c
    return 0


def caret_upper(base):
    nums, _ = split_version(base)
    if nums[0] != 0:
        return f"{nums[0] + 1}.0.0"
    if nums[1] != 0:
        return f"0.{nums[1] + 1}.0"
    return f"0.0.{nums[2] + 1}"


def in_range(version, spec):
    text = (spec or "*").strip()
    if text in {"", "*"}:
        return True
    for raw in text.split(","):
        part = raw.strip()
        if part.startswith(">="):
            ok = cmp_version(version, part[2:]) >= 0
        elif part.startswith("<="):
            ok = cmp_version(version, part[2:]) <= 0
        elif part.startswith(">"):
            ok = cmp_version(version, part[1:]) > 0
        elif part.startswith("<"):
            ok = cmp_version(version, part[1:]) < 0
        elif part.startswith("="):
            ok = cmp_version(version, part[1:]) == 0
        elif part.startswith("^"):
            base = part[1:]
            ok = cmp_version(version, base) >= 0 and cmp_version(version, caret_upper(base)) < 0
        else:
            ok = cmp_version(version, part) == 0
        if not ok:
            return False
    return True


def caps(pkg):
    out = [(pkg["name"], pkg["version"])]
    for item in pkg.get("provides", []):
        out.append((item["name"], item["version"]))
    return out


def active(dep, pkg, request):
    if not dep.get("optional", False):
        return True
    feature = dep.get("feature")
    return bool(feature and feature in request.get("features", {}).get(pkg["name"], []))


def selected_satisfies(selection, name, spec):
    return any(cap_name == name and in_range(cap_version, spec) for pkg in selection for cap_name, cap_version in caps(pkg))


def valid(selection, request):
    for root in request["roots"]:
        if not selected_satisfies(selection, root["name"], root.get("range", "*")):
            return False
    for pkg in selection:
        for dep in pkg.get("deps", []):
            if active(dep, pkg, request) and not selected_satisfies(selection, dep["name"], dep.get("range", "*")):
                return False
    for pkg in selection:
        for conflict in pkg.get("conflicts", []):
            if selected_satisfies(selection, conflict["name"], conflict.get("range", "*")):
                return False
    return True


def score(selection, lock):
    selected_by_name = {pkg["name"]: pkg for pkg in selection}
    locked_by_name = {pkg["name"]: pkg["version"] for pkg in lock.get("locked", [])}
    removed = sum(1 for name in locked_by_name if name not in selected_by_name)
    changed = sum(1 for name, version in locked_by_name.items() if name in selected_by_name and selected_by_name[name]["version"] != version)
    added = sum(1 for pkg in selection if pkg["name"] not in locked_by_name)
    key = tuple(f"{pkg['name']}@{pkg['version']}" for pkg in sorted(selection, key=lambda p: (p["name"], p["version"])))
    return removed, changed, added, key


def reference(repo, request, lock):
    by_name = {}
    for pkg in repo["packages"]:
        if not pkg.get("yanked", False):
            by_name.setdefault(pkg["name"], []).append(pkg)
    names = sorted(by_name)
    best = None
    best_score = None
    options = [[None] + sorted(by_name[name], key=lambda p: p["version"]) for name in names]
    for choices in itertools.product(*options):
        selection = [pkg for pkg in choices if pkg is not None]
        if valid(selection, request):
            current = score(selection, lock)
            if best_score is None or current < best_score:
                best = selection
                best_score = current
    if best is None:
        plan = {"status": "unsat", "selected": [], "changes": []}
        audit = [{"status": "unsat", "selected": 0, "changes": 0}, {"reason": "no valid plan"}]
        return plan, audit
    selected = sorted(({"name": pkg["name"], "version": pkg["version"]} for pkg in best), key=lambda p: (p["name"], p["version"]))
    selected_by_name = {pkg["name"]: pkg["version"] for pkg in selected}
    locked_by_name = {pkg["name"]: pkg["version"] for pkg in lock.get("locked", [])}
    changes = []
    for name, old in sorted(locked_by_name.items()):
        if name not in selected_by_name:
            changes.append({"name": name, "kind": "removed", "from": old, "to": None})
        elif selected_by_name[name] != old:
            changes.append({"name": name, "kind": "changed", "from": old, "to": selected_by_name[name]})
    for name, new in sorted(selected_by_name.items()):
        if name not in locked_by_name:
            changes.append({"name": name, "kind": "added", "from": None, "to": new})
    plan = {"status": "ok", "selected": selected, "changes": changes}
    audit = [{"status": "ok", "selected": len(selected), "changes": len(changes)}]
    audit.extend({"package": item["name"], "version": item["version"]} for item in selected)
    audit.extend({"change": item["kind"], "name": item["name"], "from": item["from"], "to": item["to"]} for item in changes)
    return plan, audit


@pytest.fixture(scope="session")
def binary():
    """Build the auditor once for all verifier cases."""
    subprocess.run([str(APP / "bin" / "build.sh")], check=True, cwd="/app", timeout=90)
    return APP / "bin" / "plock-audit"


def run_case(binary, repo, request, lock):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = {name: root / f"{name}.json" for name in ["repo", "request", "lock", "plan"]}
        audit_path = root / "audit.ndjson"
        paths["repo"].write_text(json.dumps(repo, sort_keys=True))
        paths["request"].write_text(json.dumps(request, sort_keys=True))
        paths["lock"].write_text(json.dumps(lock, sort_keys=True))
        original_inputs = {name: paths[name].read_text() for name in ["repo", "request", "lock"]}
        subprocess.run(
            [
                str(binary),
                "--repo", str(paths["repo"]),
                "--request", str(paths["request"]),
                "--lock", str(paths["lock"]),
                "--plan", str(paths["plan"]),
                "--audit", str(audit_path),
            ],
            check=True,
            cwd="/app",
            timeout=30,
        )
        assert {name: paths[name].read_text() for name in ["repo", "request", "lock"]} == original_inputs
        plan = json.loads(paths["plan"].read_text())
        audit = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        return plan, audit


def generated_case(seed):
    tag = f"p{seed}"
    root = f"{tag}-root"
    cap = f"{tag}-runtime"
    feat = f"{tag}-tls"
    bridge = f"{tag}-bridge"
    lib = f"{tag}-lib"
    old = f"{tag}-native"
    new = f"{tag}-portable"
    f1 = f"{tag}-front"
    f2 = f"{tag}-side"
    packages = [
        {"name": root, "version": "1.0.0", "deps": [
            {"name": cap, "range": ">=1.0.0,<3.0.0"},
            {"name": lib, "range": ">1.0.0-alpha,<2.0.0"},
            {"name": feat, "range": ">=1.0.0,<2.0.0", "optional": True, "feature": "extra"},
        ]},
        {"name": old, "version": "1.0.0", "provides": [{"name": cap, "version": "1.0.0"}], "conflicts": [{"name": feat, "range": ">=1.0.0,<2.0.0"}]},
        {"name": new, "version": "2.0.0", "provides": [{"name": cap, "version": "2.0.0"}], "deps": [{"name": bridge, "range": "^1.0.0"}]},
        {"name": bridge, "version": "1.0.0"},
        {"name": bridge, "version": "1.1.0", "yanked": seed % 3 == 0},
        {"name": lib, "version": "1.0.0-alpha"},
        {"name": lib, "version": "1.0.0"},
        {"name": lib, "version": "1.1.0", "yanked": seed % 2 == 0},
        {"name": f1, "version": "1.0.0", "provides": [{"name": feat, "version": "1.0.0"}], "conflicts": [{"name": cap, "range": "=2.0.0"}]},
        {"name": f2, "version": "1.0.0", "provides": [{"name": feat, "version": "1.0.0"}], "deps": [{"name": bridge, "range": ">=1.0.0,<2.0.0"}]},
    ]
    request = {"roots": [{"name": root, "range": "=1.0.0"}], "features": {root: ["extra"]}}
    lock = {"locked": [
        {"name": root, "version": "1.0.0"},
        {"name": old, "version": "1.0.0"},
        {"name": lib, "version": "1.1.0" if seed % 2 == 0 else "1.0.0"},
        {"name": f1, "version": "1.0.0"},
    ]}
    return {"packages": packages}, request, lock


def layered_case(seed):
    tag = f"h{seed}"
    root = f"{tag}-root"
    runtime = f"{tag}-runtime"
    legacy = f"{tag}-legacy-runtime"
    modern = f"{tag}-modern-runtime"
    shim = f"{tag}-shim"
    parser = f"{tag}-parser"
    guard = f"{tag}-guard"
    guard_a = f"{tag}-guard-a"
    guard_b = f"{tag}-guard-b"
    cert = f"{tag}-cert"
    ghost = f"{tag}-ghost"
    aux = f"{tag}-aux"
    packages = [
        {"name": root, "version": "1.0.0", "deps": [
            {"name": runtime, "range": ">=1.0.0,<3.0.0"},
            {"name": parser, "range": "^0.2.3"},
            {"name": aux, "range": ">=0.0.2,<0.0.4"},
            {"name": guard, "range": ">=1.0.0,<2.0.0", "optional": True, "feature": "audit"},
        ]},
        {"name": legacy, "version": "1.0.0", "provides": [{"name": runtime, "version": "1.0.0"}], "conflicts": [{"name": guard, "range": ">=1.0.0,<2.0.0"}]},
        {"name": modern, "version": "2.0.0", "provides": [{"name": runtime, "version": "2.0.0"}], "deps": [{"name": shim, "range": "^0.0.2"}]},
        {"name": shim, "version": "0.0.2"},
        {"name": shim, "version": "0.0.3"},
        {"name": parser, "version": "0.2.3"},
        {"name": parser, "version": "0.2.4-rc.1"},
        {"name": parser, "version": "0.2.9", "yanked": seed % 2 == 0},
        {"name": parser, "version": "0.3.0"},
        {"name": guard_a, "version": "1.0.0", "provides": [{"name": guard, "version": "1.0.0"}], "conflicts": [{"name": parser, "range": "=0.2.9"}]},
        {"name": guard_b, "version": "1.1.0", "provides": [{"name": guard, "version": "1.1.0"}], "deps": [{"name": cert, "range": ">=1.0.0,<2.0.0"}]},
        {"name": cert, "version": "1.0.0"},
        {"name": ghost, "version": "1.0.0", "conflicts": [{"name": runtime, "range": "*"}]},
        {"name": aux, "version": "0.0.2"},
        {"name": aux, "version": "0.0.3"},
        {"name": aux, "version": "0.0.4", "yanked": True},
    ]
    request = {"roots": [{"name": root, "range": "=1.0.0"}], "features": {root: ["audit"]}}
    lock = {"locked": [
        {"name": root, "version": "1.0.0"},
        {"name": legacy, "version": "1.0.0"},
        {"name": parser, "version": "0.2.9"},
        {"name": guard_a, "version": "1.0.0"},
        {"name": ghost, "version": "1.0.0"},
        {"name": aux, "version": "0.0.3"},
    ]}
    return {"packages": packages}, request, lock


def assert_case_matches(binary, repo, request, lock):
    expected_plan, expected_audit = reference(repo, request, lock)
    actual_plan, actual_audit = run_case(binary, repo, request, lock)
    assert actual_plan == expected_plan
    assert actual_audit == expected_audit


def test_generated_lock_plans_match_reference(binary):
    """Generated repositories must resolve to the same deterministic lock plan as the reference model."""
    for seed in range(2, 13):
        assert_case_matches(binary, *generated_case(seed))


def test_layered_lock_scoring_requires_non_local_choice(binary):
    """Lock minimization must consider removals before changed versions across interacting providers."""
    for seed in range(1, 6):
        repo, request, lock = layered_case(seed)
        actual_plan, actual_audit = run_case(binary, repo, request, lock)
        expected_plan, expected_audit = reference(repo, request, lock)
        assert actual_plan == expected_plan
        assert actual_audit == expected_audit
        selected = {item["name"]: item["version"] for item in actual_plan["selected"]}
        tag = f"h{seed}"
        assert selected[f"{tag}-parser"] == "0.2.3"
        assert f"{tag}-guard-a" in selected
        assert f"{tag}-guard-b" not in selected
        assert f"{tag}-cert" not in selected
        assert f"{tag}-legacy-runtime" not in selected
        assert f"{tag}-ghost" not in selected


def test_capability_roots_caret_zero_and_prerelease_ordering(binary):
    """A root may be provided by a capability whose 0.x caret range includes prerelease ordering."""
    repo = {"packages": [
        {"name": "adapter-rc", "version": "7.0.0", "provides": [{"name": "service-api", "version": "0.2.4-rc.1"}]},
        {"name": "adapter-stable", "version": "7.0.1", "provides": [{"name": "service-api", "version": "0.2.3"}]},
        {"name": "adapter-next", "version": "8.0.0", "provides": [{"name": "service-api", "version": "0.3.0"}]},
    ]}
    request = {"roots": [{"name": "service-api", "range": "^0.2.3"}]}
    lock = {"locked": [{"name": "adapter-rc", "version": "7.0.0"}]}
    plan, audit = run_case(binary, repo, request, lock)
    expected_plan, expected_audit = reference(repo, request, lock)
    assert plan == expected_plan
    assert audit == expected_audit
    assert plan["selected"] == [{"name": "adapter-rc", "version": "7.0.0"}]


def test_missing_ranges_default_to_star_for_roots_deps_and_conflicts(binary):
    """Omitted root, dependency, and conflict ranges must all behave like the wildcard range."""
    repo = {"packages": [
        {"name": "app", "version": "1.0.0", "deps": [{"name": "runtime"}]},
        {"name": "runtime-a", "version": "1.0.0", "provides": [{"name": "runtime", "version": "1.0.0"}], "conflicts": [{"name": "sentinel"}]},
        {"name": "runtime-b", "version": "2.0.0", "provides": [{"name": "runtime", "version": "2.0.0"}]},
        {"name": "sentinel", "version": "9.9.9"},
    ]}
    request = {"roots": [{"name": "app"}, {"name": "sentinel"}]}
    lock = {"locked": [
        {"name": "app", "version": "1.0.0"},
        {"name": "runtime-a", "version": "1.0.0"},
        {"name": "sentinel", "version": "9.9.9"},
    ]}
    plan, audit = run_case(binary, repo, request, lock)
    assert plan == {
        "status": "ok",
        "selected": [
            {"name": "app", "version": "1.0.0"},
            {"name": "runtime-b", "version": "2.0.0"},
            {"name": "sentinel", "version": "9.9.9"},
        ],
        "changes": [
            {"name": "runtime-a", "kind": "removed", "from": "1.0.0", "to": None},
            {"name": "runtime-b", "kind": "added", "from": None, "to": "2.0.0"},
        ],
    }
    assert audit == [
        {"status": "ok", "selected": 3, "changes": 2},
        {"package": "app", "version": "1.0.0"},
        {"package": "runtime-b", "version": "2.0.0"},
        {"package": "sentinel", "version": "9.9.9"},
        {"change": "removed", "name": "runtime-a", "from": "1.0.0", "to": None},
        {"change": "added", "name": "runtime-b", "from": None, "to": "2.0.0"},
    ]


def test_less_than_or_equal_range_keeps_locked_boundary_provider(binary):
    """The <= comparator must include a provider exactly on the upper boundary."""
    repo = {"packages": [
        {"name": "app", "version": "1.0.0", "deps": [{"name": "api", "range": ">=1.0.0,<=1.0.1"}]},
        {"name": "floor-provider", "version": "1.0.0", "provides": [{"name": "api", "version": "1.0.0"}]},
        {"name": "boundary-provider", "version": "1.0.0", "provides": [{"name": "api", "version": "1.0.1"}]},
        {"name": "above-provider", "version": "1.0.0", "provides": [{"name": "api", "version": "1.0.2"}]},
    ]}
    request = {"roots": [{"name": "app", "range": "=1.0.0"}]}
    lock = {"locked": [
        {"name": "app", "version": "1.0.0"},
        {"name": "boundary-provider", "version": "1.0.0"},
    ]}
    plan, audit = run_case(binary, repo, request, lock)
    expected_plan, expected_audit = reference(repo, request, lock)
    assert plan == expected_plan == {
        "status": "ok",
        "selected": [
            {"name": "app", "version": "1.0.0"},
            {"name": "boundary-provider", "version": "1.0.0"},
        ],
        "changes": [],
    }
    assert audit == expected_audit == [
        {"status": "ok", "selected": 2, "changes": 0},
        {"package": "app", "version": "1.0.0"},
        {"package": "boundary-provider", "version": "1.0.0"},
    ]


def test_numeric_prerelease_identifiers_sort_before_text_identifiers(binary):
    """SemVer prerelease comparison must treat numeric identifiers as lower than text identifiers."""
    repo = {"packages": [
        {"name": "app", "version": "1.0.0", "deps": [{"name": "api", "range": ">1.0.0-1,<1.0.0"}]},
        {"name": "numeric-provider", "version": "1.0.0", "provides": [{"name": "api", "version": "1.0.0-1"}]},
        {"name": "alpha-provider", "version": "1.0.0", "provides": [{"name": "api", "version": "1.0.0-alpha"}]},
        {"name": "release-provider", "version": "1.0.0", "provides": [{"name": "api", "version": "1.0.0"}]},
    ]}
    request = {"roots": [{"name": "app", "range": "=1.0.0"}]}
    lock = {"locked": [
        {"name": "app", "version": "1.0.0"},
        {"name": "numeric-provider", "version": "1.0.0"},
    ]}
    plan, audit = run_case(binary, repo, request, lock)
    assert plan == {
        "status": "ok",
        "selected": [
            {"name": "alpha-provider", "version": "1.0.0"},
            {"name": "app", "version": "1.0.0"},
            {"name": "numeric-provider", "version": "1.0.0"},
        ],
        "changes": [
            {"name": "alpha-provider", "kind": "added", "from": None, "to": "1.0.0"},
        ],
    }
    assert audit == [
        {"status": "ok", "selected": 3, "changes": 1},
        {"package": "alpha-provider", "version": "1.0.0"},
        {"package": "app", "version": "1.0.0"},
        {"package": "numeric-provider", "version": "1.0.0"},
        {"change": "added", "name": "alpha-provider", "from": None, "to": "1.0.0"},
    ]


def test_optional_deps_are_activated_only_by_requested_features(binary):
    """Feature-gated deps must be absent when inactive and present with their transitive deps when active."""
    repo = {"packages": [
        {"name": "app", "version": "1.0.0", "deps": [
            {"name": "core", "range": "=1.0.0"},
            {"name": "plugin-api", "range": ">=1.0.0,<2.0.0", "optional": True, "feature": "boost"},
        ]},
        {"name": "core", "version": "1.0.0"},
        {"name": "plugin", "version": "1.0.0", "provides": [{"name": "plugin-api", "version": "1.0.0"}], "deps": [{"name": "leaf", "range": "=1.0.0"}]},
        {"name": "leaf", "version": "1.0.0"},
    ]}
    lock = {"locked": [{"name": "app", "version": "1.0.0"}, {"name": "core", "version": "1.0.0"}]}
    inactive_plan, _ = run_case(binary, repo, {"roots": [{"name": "app", "range": "=1.0.0"}]}, lock)
    active_plan, active_audit = run_case(binary, repo, {"roots": [{"name": "app", "range": "=1.0.0"}], "features": {"app": ["boost"]}}, lock)
    assert inactive_plan == reference(repo, {"roots": [{"name": "app", "range": "=1.0.0"}]}, lock)[0]
    expected_active_plan, expected_active_audit = reference(repo, {"roots": [{"name": "app", "range": "=1.0.0"}], "features": {"app": ["boost"]}}, lock)
    assert active_plan == expected_active_plan
    assert active_audit == expected_active_audit
    assert inactive_plan["selected"] == [{"name": "app", "version": "1.0.0"}, {"name": "core", "version": "1.0.0"}]
    assert {item["name"] for item in active_plan["selected"]} == {"app", "core", "plugin", "leaf"}


def test_unsatisfied_plan_is_reported_deterministically(binary):
    """A repository with no legal package set must produce the stable unsatisfied form."""
    repo = {"packages": [
        {"name": "root", "version": "1.0.0", "deps": [{"name": "left", "range": "*"}, {"name": "right", "range": "*"}]},
        {"name": "a", "version": "1.0.0", "provides": [{"name": "left", "version": "1.0.0"}], "conflicts": [{"name": "right", "range": "*"}]},
        {"name": "b", "version": "1.0.0", "provides": [{"name": "right", "version": "1.0.0"}], "conflicts": [{"name": "left", "range": "*"}]},
    ]}
    request = {"roots": [{"name": "root", "range": "=1.0.0"}]}
    lock = {"locked": [{"name": "root", "version": "1.0.0"}, {"name": "a", "version": "1.0.0"}]}
    assert_case_matches(binary, repo, request, lock)


def test_conflicts_include_own_capabilities_and_change_order(binary):
    """Conflict checks include all selected capabilities, and change records keep the specified grouping."""
    repo = {"packages": [
        {"name": "app", "version": "1.0.0", "deps": [{"name": "svc", "range": "*"}]},
        {"name": "self-ban", "version": "1.0.0", "provides": [{"name": "svc", "version": "1.0.0"}], "conflicts": [{"name": "svc", "range": "*"}]},
        {"name": "ok-svc", "version": "1.0.0", "provides": [{"name": "svc", "version": "1.0.0"}]},
    ]}
    request = {"roots": [{"name": "app", "range": "=1.0.0"}]}
    lock = {"locked": [{"name": "app", "version": "0.9.0"}, {"name": "self-ban", "version": "1.0.0"}]}
    plan, audit = run_case(binary, repo, request, lock)
    expected_plan, expected_audit = reference(repo, request, lock)
    assert plan == expected_plan
    assert audit == expected_audit
    assert plan["selected"] == [{"name": "app", "version": "1.0.0"}, {"name": "ok-svc", "version": "1.0.0"}]
    assert plan["changes"] == [
        {"name": "app", "kind": "changed", "from": "0.9.0", "to": "1.0.0"},
        {"name": "self-ban", "kind": "removed", "from": "1.0.0", "to": None},
        {"name": "ok-svc", "kind": "added", "from": None, "to": "1.0.0"},
    ]


def test_repeated_runs_are_identical(binary):
    """The same inputs must produce byte-stable JSON data across separate invocations."""
    repo, request, lock = generated_case(12)
    first = run_case(binary, repo, request, lock)
    second = run_case(binary, repo, request, lock)
    assert first == second == reference(repo, request, lock)
