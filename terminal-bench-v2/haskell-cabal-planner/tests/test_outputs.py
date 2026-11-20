import json
import subprocess


def run_plan(tmp_path, repo_text):
    repo = tmp_path / "repo.plan"
    out = tmp_path / "plan.json"
    repo.write_text(repo_text.strip() + "\n")
    subprocess.run(["/app/cabal_plan", str(repo), str(out)], check=True, cwd="/app")
    return json.loads(out.read_text())


def package(plan, name):
    matches = [p for p in plan["packages"] if p["name"] == name]
    assert len(matches) == 1
    return matches[0]


def assert_common_shape(plan):
    assert sorted(plan) == ["build_order", "packages"]
    ids = [p["id"] for p in plan["packages"]]
    assert ids == sorted(ids)
    for p in plan["packages"]:
        assert sorted(p) == [
            "depends",
            "flags",
            "id",
            "name",
            "needs",
            "provides",
            "version",
        ]
        assert p["id"] == f"{p['name']}-{p['version']}"
        assert p["depends"] == sorted(p["depends"])
        assert [f["name"] for f in p["flags"]] == sorted(f["name"] for f in p["flags"])


def run_unsat(tmp_path, repo_text):
    repo = tmp_path / "repo.plan"
    out = tmp_path / "plan.json"
    repo.write_text(repo_text.strip() + "\n")
    result = subprocess.run(
        ["/app/cabal_plan", str(repo), str(out)],
        cwd="/app",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert not out.exists() or out.read_text() == ""


def test_plan_respects_flags_and_module_abis(tmp_path):
    """The selected closure must honor flags and module compatibility together."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 api>=1
        flag app 1.0 impl True fast>=1 safe>=1
        pkg api 1.0
        need api 1.0 Codec v2
        pkg fast 2.0
        provide fast 2.0 Codec v1
        pkg safe 1.0
        provide safe 1.0 Codec v2
        root app=1.0
        """,
    )
    assert_common_shape(plan)
    assert [p["id"] for p in plan["packages"]] == ["api-1.0", "app-1.0", "safe-1.0"]
    assert package(plan, "app")["flags"] == [{"name": "impl", "value": False}]
    assert package(plan, "app")["depends"] == ["api-1.0", "safe-1.0"]
    providers = {
        (x["module"], x["abi"]) for p in plan["packages"] for x in p["provides"]
    }
    for p in plan["packages"]:
        for n in p["needs"]:
            assert (n["module"], n["abi"]) in providers


def test_abi_matching_rejects_same_module_wrong_abi_decoys(tmp_path):
    """Module compatibility requires an exact ABI match, not only a module name."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 iface>=1,consumer=1.0
        pkg consumer 1.0
        need consumer 1.0 Data.Array abi-2
        pkg iface 1.0
        provide iface 1.0 Data.Array abi-2
        pkg iface 2.0
        provide iface 2.0 Data.Array abi-1
        root app=1.0
        """,
    )
    assert package(plan, "iface")["version"] == "1.0"
    assert package(plan, "consumer")["needs"] == [
        {"module": "Data.Array", "abi": "abi-2"}
    ]
    providers = {
        (x["module"], x["abi"]) for p in plan["packages"] for x in p["provides"]
    }
    assert ("Data.Array", "abi-2") in providers
    assert ("Data.Array", "abi-1") not in providers


def test_unsatisfied_need_with_wrong_abi_reports_failure(tmp_path):
    """A selected need is unsatisfiable when providers share only the module name."""
    run_unsat(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 consumer=1.0,iface=1.0
        pkg consumer 1.0
        need consumer 1.0 Data.Array abi-2
        pkg iface 1.0
        provide iface 1.0 Data.Array abi-1
        root app=1.0
        """,
    )


def test_numeric_version_preference_is_stable(tmp_path):
    """Numeric package choices must be reproducible across repeated planner runs."""
    repo = """
        pkg root 1.0
        dep root 1.0 lib>=1
        pkg lib 1.2
        pkg lib 1.10
        root root=1.0
    """
    first = run_plan(tmp_path, repo)
    second = run_plan(tmp_path, repo)
    assert first == second
    assert package(first, "lib")["version"] == "1.10"
    assert package(first, "root")["depends"] == ["lib-1.10"]


def test_minimal_plan_accepts_empty_requirement_sentinel(tmp_path):
    """Packages with no active requirements still produce a complete plan."""
    plan = run_plan(
        tmp_path,
        """
        pkg solo 1.0
        dep solo 1.0 -
        flag solo 1.0 docs False - -
        root solo=1.0
        """,
    )
    assert_common_shape(plan)
    assert plan["packages"] == [
        {
            "name": "solo",
            "version": "1.0",
            "id": "solo-1.0",
            "flags": [{"name": "docs", "value": False}],
            "depends": [],
            "provides": [],
            "needs": [],
        }
    ]
    assert plan["build_order"] == [["solo-1.0"]]


def test_constraint_operators_choose_highest_valid_versions(tmp_path):
    """Each comparison operator filters candidates before highest-version choice."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 ge>=1.2,le<=2.0,lt<3.0,gt>1.0
        pkg ge 1.1
        pkg ge 1.2
        pkg ge 2.5
        pkg le 1.0
        pkg le 2.0
        pkg le 2.1
        pkg lt 2.9
        pkg lt 3.0
        pkg gt 1.0
        pkg gt 1.1
        root app=1.0
        """,
    )
    assert package(plan, "ge")["version"] == "2.5"
    assert package(plan, "le")["version"] == "2.0"
    assert package(plan, "lt")["version"] == "2.9"
    assert package(plan, "gt")["version"] == "1.1"
    assert package(plan, "app")["depends"] == ["ge-2.5", "gt-1.1", "le-2.0", "lt-2.9"]


def test_flag_deviation_is_tiebreaker_after_equal_versions(tmp_path):
    """When selected versions tie, the planner keeps more default flag values."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        flag app 1.0 first True lib=1.0 lib=1.0
        flag app 1.0 second False lib=1.0 lib=1.0
        pkg lib 1.0
        root app=1.0
        """,
    )
    assert package(plan, "app")["flags"] == [
        {"name": "first", "value": True},
        {"name": "second", "value": False},
    ]
    assert package(plan, "app")["depends"] == ["lib-1.0"]


def test_dependency_cycles_are_grouped_in_build_order(tmp_path):
    """Dependency-first build order must keep cyclic components together."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 lens=1.0
        pkg lens 1.0
        dep lens 1.0 prof=1.0
        pkg prof 1.0
        dep prof 1.0 lens=1.0
        root app=1.0
        """,
    )
    assert plan["build_order"] == [["lens-1.0", "prof-1.0"], ["app-1.0"]]
    assert package(plan, "lens")["depends"] == ["prof-1.0"]
    assert package(plan, "prof")["depends"] == ["lens-1.0"]


def test_larger_mutual_dependency_component_orders_dependents_afterward(tmp_path):
    """A multi-package cycle is one build group before its acyclic dependents."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 worker=1.0,ui=1.0
        pkg worker 1.0
        dep worker 1.0 core=1.0
        pkg ui 1.0
        dep ui 1.0 core=1.0
        pkg core 1.0
        dep core 1.0 codec=1.0
        pkg codec 1.0
        dep codec 1.0 core=1.0,wire=1.0
        pkg wire 1.0
        dep wire 1.0 codec=1.0
        root app=1.0
        """,
    )
    assert plan["build_order"] == [
        ["codec-1.0", "core-1.0", "wire-1.0"],
        ["ui-1.0"],
        ["worker-1.0"],
        ["app-1.0"],
    ]
    assert package(plan, "core")["depends"] == ["codec-1.0"]
    assert package(plan, "codec")["depends"] == ["core-1.0", "wire-1.0"]


def test_package_name_priority_precedes_later_ties(tmp_path):
    """When alternatives remain valid, earlier package-name versions decide the plan."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        flag app 1.0 mode True alpha=1.0,beta=9.0 alpha=2.0,beta=1.0
        pkg alpha 1.0
        pkg alpha 2.0
        pkg beta 1.0
        pkg beta 9.0
        root app=1.0
        """,
    )
    assert package(plan, "app")["flags"] == [{"name": "mode", "value": False}]
    assert package(plan, "alpha")["version"] == "2.0"
    assert package(plan, "beta")["version"] == "1.0"


def test_package_name_version_priority_isolated_from_flags(tmp_path):
    """Alphabetically earlier package versions decide even without flag choices."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 selector>=1
        pkg selector 1.0
        dep selector 1.0 alpha=2.0,beta=1.0
        pkg selector 2.0
        dep selector 2.0 alpha=1.0,beta=9.0
        pkg alpha 1.0
        pkg alpha 2.0
        pkg beta 1.0
        pkg beta 9.0
        root app=1.0
        """,
    )
    assert package(plan, "alpha")["version"] == "2.0"
    assert package(plan, "beta")["version"] == "1.0"
    assert package(plan, "selector")["version"] == "1.0"


def test_conflicts_force_global_backtracking_across_flag_choices(tmp_path):
    """The best plan must backtrack around incompatible high versions."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        flag app 1.0 route True api>=2 api>=1
        pkg api 1.0
        dep api 1.0 shim=1.0
        provide api 1.0 Codec stable
        pkg api 2.0
        dep api 2.0 fast=2.0
        provide api 2.0 Codec fast
        conflict api 2.0 shim=1.0
        pkg shim 1.0
        need shim 1.0 Codec stable
        pkg fast 2.0
        need fast 2.0 Codec stable
        root app=1.0
        root shim=1.0
        """,
    )
    assert package(plan, "app")["flags"] == [{"name": "route", "value": False}]
    assert package(plan, "api")["version"] == "1.0"
    assert package(plan, "app")["depends"] == ["api-1.0"]
    assert plan["build_order"] == [["shim-1.0"], ["api-1.0"], ["app-1.0"]]


def test_conflict_requirements_match_active_selected_versions(tmp_path):
    """Conflict requirements apply by package name and version predicate."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        flag app 1.0 flavor True plugin>=1,adapter=1.0 plugin>=1,adapter=1.0
        conflict app 1.0 plugin>=2
        pkg plugin 1.0
        provide plugin 1.0 Hook stable
        pkg plugin 2.0
        provide plugin 2.0 Hook stable
        pkg adapter 1.0
        need adapter 1.0 Hook stable
        root app=1.0
        """,
    )
    assert package(plan, "plugin")["version"] == "1.0"
    assert package(plan, "adapter")["needs"] == [{"module": "Hook", "abi": "stable"}]
    assert package(plan, "app")["flags"] == [{"name": "flavor", "value": True}]


def test_conflict_only_repository_reports_failure(tmp_path):
    """A selected conflict requirement alone makes the repository unsatisfiable."""
    run_unsat(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 plugin=1.0
        conflict app 1.0 plugin=1.0
        pkg plugin 1.0
        root app=1.0
        """,
    )


def test_interacting_locks_conflicts_abis_and_scc_ordering(tmp_path):
    """The planner must combine lock, conflict, ABI, and SCC rules in one plan."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 driver>=1,frontend=1.0
        flag app 1.0 backend True driver>=2,shim=2.0 driver>=1,shim=1.0
        pkg frontend 1.0
        need frontend 1.0 API stable
        dep frontend 1.0 core=1.0
        pkg core 1.0
        dep core 1.0 util=1.0
        pkg util 1.0
        dep util 1.0 core=1.0
        pkg driver 1.0
        provide driver 1.0 API stable
        pkg driver 2.0
        provide driver 2.0 API unstable
        conflict driver 2.0 shim=1.0
        pkg shim 1.0
        need shim 1.0 API stable
        pkg shim 2.0
        need shim 2.0 API unstable
        lock shim 1.0
        root app=1.0
        """,
    )
    assert package(plan, "app")["flags"] == [{"name": "backend", "value": False}]
    assert package(plan, "driver")["version"] == "1.0"
    assert package(plan, "shim")["version"] == "1.0"
    assert plan["build_order"] == [
        ["core-1.0", "util-1.0"],
        ["driver-1.0"],
        ["frontend-1.0"],
        ["shim-1.0"],
        ["app-1.0"],
    ]


def test_locks_constrain_transitive_selection_and_preserve_order(tmp_path):
    """A lock applies to transitive requirements and preserves DAG order."""
    plan = run_plan(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 solver>=1,tool=1.0
        pkg solver 1.0
        pkg solver 2.0
        dep solver 2.0 addon=2.0
        pkg addon 2.0
        pkg tool 1.0
        dep tool 1.0 solver>=1
        lock solver 1.0
        root app=1.0
        """,
    )
    assert package(plan, "solver")["version"] == "1.0"
    assert package(plan, "app")["depends"] == ["solver-1.0", "tool-1.0"]
    assert plan["build_order"] == [["solver-1.0"], ["tool-1.0"], ["app-1.0"]]


def test_unsatisfiable_lock_and_conflict_reports_failure(tmp_path):
    """Incompatible locks and conflicts must reject the repository."""
    run_unsat(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 core>=2
        pkg core 1.0
        pkg core 2.0
        lock core 1.0
        root app=1.0
        """,
    )
    run_unsat(
        tmp_path,
        """
        pkg app 1.0
        dep app 1.0 plugin=1.0
        conflict app 1.0 plugin=1.0
        pkg plugin 1.0
        root app=1.0
        """,
    )
