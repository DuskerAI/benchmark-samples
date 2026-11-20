import csv
import subprocess
from pathlib import Path

APP = Path("/app/unitplan")
BIN = APP / "bin" / "unitplan"


def build_planner() -> None:
    result = subprocess.run(
        [str(APP / "scripts" / "build.sh")],
        cwd=APP,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert BIN.exists(), "build did not create /app/unitplan/bin/unitplan"


def write_unit(root: Path, name: str, body: str) -> None:
    (root / "units").mkdir(parents=True, exist_ok=True)
    (root / "units" / name).write_text(body.strip() + "\n", encoding="utf-8")


def write_dropin(root: Path, unit: str, name: str, body: str) -> None:
    d = root / "dropins" / f"{unit}.d"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body.strip() + "\n", encoding="utf-8")


def write_state(root: Path, states: dict[str, str]) -> None:
    lines = ["unit\tstate"] + [
        f"{unit}\t{state}" for unit, state in sorted(states.items())
    ]
    (root / "state.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_plan(root: Path, change: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BIN), "--root", str(root), "--change", str(change), "--out", str(out)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def write_change(path: Path, body: str) -> None:
    path.write_text(body.strip() + "\n", encoding="utf-8")


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def read_tsv_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    return rows[0], rows[1:]


def assert_rejected_without_output(root: Path, change: Path, out: Path) -> None:
    result = run_plan(root, change, out)
    assert result.returncode != 0
    assert not out.exists()


def test_dropins_propagation_and_dependency_batches(tmp_path: Path) -> None:
    """Drop-ins override base metadata before propagated restarts are ordered into deterministic batches."""
    build_planner()
    root = tmp_path / "case"
    out = tmp_path / "out"
    write_unit(root, "maintenance.target", "[Unit]\nDescription=window")
    write_unit(root, "db.service", "[Unit]\nDescription=db")
    write_unit(root, "cache.service", "[Unit]\nDescription=cache")
    write_unit(
        root,
        "api.service",
        """
        [Unit]
        Requires=db.service
        After=db.service
        PartOf=old.target
        [Service]
        RefuseManualRestart=true
        ConditionPathExists=/run/api.ready
        """,
    )
    write_unit(
        root,
        "frontend.service",
        "[Unit]\nPartOf=maintenance.target\nAfter=api.service\nBefore=notify.service",
    )
    write_unit(
        root,
        "notify.service",
        "[Unit]\nPartOf=maintenance.target\nAfter=frontend.service",
    )
    write_unit(root, "worker.service", "[Unit]\nPartOf=api.service\nAfter=api.service")
    write_dropin(
        root,
        "api.service",
        "10-window.conf",
        "[Unit]\nPartOf=maintenance.target\nAfter=cache.service",
    )
    write_dropin(
        root, "api.service", "20-allow.conf", "[Service]\nRefuseManualRestart=false"
    )
    (root / "fs" / "run").mkdir(parents=True)
    (root / "fs" / "run" / "api.ready").write_text("ok\n", encoding="utf-8")
    write_state(
        root,
        dict.fromkeys(
            [
                "maintenance.target",
                "db.service",
                "cache.service",
                "api.service",
                "frontend.service",
                "notify.service",
                "worker.service",
            ],
            "active",
        ),
    )
    change = tmp_path / "change.txt"
    write_change(change, "restart maintenance.target")

    result = run_plan(root, change, out)
    assert result.returncode == 0, result.stderr + result.stdout
    assert sorted(p.name for p in out.iterdir()) == [
        "effective.tsv",
        "plan.tsv",
        "rollback.tsv",
    ]
    plan_header, plan_rows = read_tsv_rows(out / "plan.tsv")
    rollback_header, rollback_rows = read_tsv_rows(out / "rollback.tsv")
    effective_header, _ = read_tsv_rows(out / "effective.tsv")
    assert plan_header == ["batch", "order", "unit", "action", "reason"]
    assert rollback_header == ["boundary", "units"]
    assert effective_header == [
        "unit",
        "requires",
        "after",
        "before",
        "part_of",
        "refuse_manual_restart",
        "condition_path_exists",
        "can_reload",
        "state",
    ]
    assert [row[0] for row in plan_rows] == ["1", "2", "3", "3", "4"]
    assert [row[0] for row in rollback_rows] == ["1", "2", "3", "4"]
    plan = read_tsv(out / "plan.tsv")
    assert [(r["batch"], r["unit"], r["reason"]) for r in plan] == [
        ("1", "maintenance.target", "requested"),
        ("2", "api.service", "propagated:maintenance.target"),
        ("3", "frontend.service", "propagated:maintenance.target"),
        ("3", "worker.service", "propagated:api.service"),
        ("4", "notify.service", "propagated:maintenance.target"),
    ]
    assert [r["order"] for r in plan] == ["1", "2", "3", "4", "5"]
    assert {r["action"] for r in plan} == {"restart"}
    rollback = read_tsv(out / "rollback.tsv")
    assert [(r["boundary"], r["units"]) for r in rollback] == [
        ("1", "maintenance.target"),
        ("2", "api.service"),
        ("3", "frontend.service,worker.service"),
        ("4", "notify.service"),
    ]
    effective = {r["unit"]: r for r in read_tsv(out / "effective.tsv")}
    assert effective["api.service"]["part_of"] == "maintenance.target"
    assert effective["api.service"]["after"] == "cache.service"
    assert effective["api.service"]["refuse_manual_restart"] == "false"
    assert effective["api.service"]["condition_path_exists"] == "/run/api.ready"


def test_cycle_rejection_preserves_existing_output(tmp_path: Path) -> None:
    """Ordering cycles are rejected without replacing or adding output artifacts."""
    build_planner()
    root = tmp_path / "cycle"
    out = tmp_path / "out"
    write_unit(root, "window.target", "[Unit]")
    write_unit(
        root, "alpha.service", "[Unit]\nPartOf=window.target\nAfter=beta.service"
    )
    write_unit(
        root, "beta.service", "[Unit]\nPartOf=window.target\nAfter=alpha.service"
    )
    write_state(
        root,
        {
            "window.target": "active",
            "alpha.service": "active",
            "beta.service": "active",
        },
    )
    change = tmp_path / "change.txt"
    write_change(change, "restart window.target")
    out.mkdir()
    (out / "plan.tsv").write_text("sentinel\n", encoding="utf-8")
    before = {p.name: p.read_text(encoding="utf-8") for p in out.iterdir()}

    result = run_plan(root, change, out)
    assert result.returncode != 0
    after = {p.name: p.read_text(encoding="utf-8") for p in out.iterdir()}
    assert after == before
    assert not any(p.name.startswith("out.tmp") for p in tmp_path.iterdir())


def test_required_and_condition_safety_blocks_partial_window(tmp_path: Path) -> None:
    """Required inactive units and missing condition paths make the dossier unsafe and leave outputs absent."""
    build_planner()
    root = tmp_path / "unsafe"
    write_unit(root, "window.target", "[Unit]")
    write_unit(root, "db.service", "[Unit]")
    write_unit(
        root,
        "api.service",
        "[Unit]\nPartOf=window.target\nRequires=db.service\nConditionPathExists=/run/api.ready",
    )
    write_state(
        root,
        {"window.target": "active", "db.service": "inactive", "api.service": "active"},
    )
    change = tmp_path / "change.txt"
    write_change(change, "restart window.target")
    out = tmp_path / "out"

    result = run_plan(root, change, out)
    assert result.returncode != 0
    assert not out.exists()

    write_state(
        root,
        {"window.target": "active", "db.service": "active", "api.service": "active"},
    )
    result = run_plan(root, change, out)
    assert result.returncode != 0
    assert not out.exists()

    (root / "fs" / "run").mkdir(parents=True)
    (root / "fs" / "run" / "api.ready").write_text("ready\n", encoding="utf-8")
    result = run_plan(root, change, out)
    assert result.returncode == 0, result.stderr + result.stdout


def test_empty_dropin_resets_lists_and_ready_batches_are_lexical(
    tmp_path: Path,
) -> None:
    """Empty list assignments clear earlier edges, and every ready batch is emitted by unit name."""
    build_planner()
    root = tmp_path / "reset"
    write_unit(root, "window.target", "[Unit]")
    write_unit(
        root, "alpha.service", "[Unit]\nPartOf=window.target\n[Service]\nCanReload=yes"
    )
    write_unit(
        root,
        "mu.service",
        "[Unit]\nPartOf=window.target\nAfter=zeta.service\nConditionPathExists=/missing",
    )
    write_unit(
        root,
        "zeta.service",
        "[Unit]\nPartOf=window.target\n[Service]\nRefuseManualRestart=no",
    )
    write_dropin(
        root, "mu.service", "05-reset.conf", "[Unit]\nAfter=\nConditionPathExists="
    )
    write_dropin(root, "mu.service", "10-before.conf", "[Unit]\nBefore=zeta.service")
    write_state(
        root,
        {
            "window.target": "active",
            "alpha.service": "active",
            "mu.service": "active",
            "zeta.service": "active",
        },
    )
    change = tmp_path / "change.txt"
    write_change(change, "# planned target\nrestart window.target")
    out = tmp_path / "out"

    result = run_plan(root, change, out)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = read_tsv(out / "plan.tsv")
    assert [(r["batch"], r["unit"]) for r in plan] == [
        ("1", "window.target"),
        ("2", "alpha.service"),
        ("2", "mu.service"),
        ("3", "zeta.service"),
    ]
    effective = {r["unit"]: r for r in read_tsv(out / "effective.tsv")}
    assert effective["alpha.service"]["can_reload"] == "true"
    assert effective["mu.service"]["after"] == ""
    assert effective["mu.service"]["before"] == "zeta.service"
    assert effective["mu.service"]["condition_path_exists"] == ""
    assert effective["zeta.service"]["refuse_manual_restart"] == "false"


def test_bad_change_requests_and_requested_unit_safety_reject(tmp_path: Path) -> None:
    """Malformed changes, unknown units, and unsafe requested units are rejected before artifacts appear."""
    build_planner()

    for idx, body in enumerate(
        ["restart", "reload good.service", "restart ghost.service"]
    ):
        root = tmp_path / f"bad_change_{idx}"
        write_unit(root, "good.service", "[Unit]")
        write_state(root, {"good.service": "active"})
        change = root / "change.txt"
        write_change(change, body)
        assert_rejected_without_output(root, change, root / "out")

    root = tmp_path / "requested_safety"
    write_unit(root, "inactive.service", "[Unit]")
    write_unit(root, "refuser.service", "[Service]\nRefuseManualRestart=yes")
    write_unit(root, "conditioned.service", "[Unit]\nConditionPathExists=/run/ok")
    write_state(
        root,
        {
            "inactive.service": "inactive",
            "refuser.service": "active",
            "conditioned.service": "active",
        },
    )
    for unit in ["inactive.service", "refuser.service", "conditioned.service"]:
        change = root / f"{unit}.change"
        write_change(change, f"restart {unit}")
        assert_rejected_without_output(root, change, root / f"out-{unit}")


def test_requires_targets_must_exist_and_be_safe(tmp_path: Path) -> None:
    """Requires targets are checked even when they are not part of the restart closure."""
    build_planner()

    root = tmp_path / "missing_require"
    write_unit(root, "app.service", "[Unit]\nRequires=missing.service")
    write_state(root, {"app.service": "active"})
    change = root / "change.txt"
    write_change(change, "restart app.service")
    assert_rejected_without_output(root, change, root / "out")

    root = tmp_path / "unsafe_require"
    write_unit(root, "app.service", "[Unit]\nRequires=db.service cache.service")
    write_unit(root, "db.service", "[Service]\nRefuseManualRestart=true")
    write_unit(root, "cache.service", "[Unit]\nConditionPathExists=/run/cache.ready")
    write_state(
        root,
        {"app.service": "active", "db.service": "active", "cache.service": "active"},
    )
    change = root / "change.txt"
    write_change(change, "restart app.service")
    assert_rejected_without_output(root, change, root / "out-refuse-and-condition")

    write_dropin(
        root, "db.service", "10-safe.conf", "[Service]\nRefuseManualRestart=false"
    )
    assert_rejected_without_output(root, change, root / "out-condition")


def test_requires_edges_and_multiple_reasons_are_deterministic(tmp_path: Path) -> None:
    """Requires creates restart ordering, and multiple propagation reasons are semicolon-sorted."""
    build_planner()
    root = tmp_path / "requires_edges"
    out = tmp_path / "out"
    write_unit(root, "alpha.target", "[Unit]")
    write_unit(root, "beta.target", "[Unit]")
    write_unit(root, "db.service", "[Unit]")
    write_unit(root, "web.service", "[Unit]\nRequires=db.service")
    write_unit(root, "shared.service", "[Unit]\nPartOf=beta.target alpha.target")
    write_state(
        root,
        {
            "alpha.target": "active",
            "beta.target": "active",
            "db.service": "active",
            "web.service": "active",
            "shared.service": "active",
        },
    )
    change = tmp_path / "change.txt"
    write_change(
        change,
        "restart web.service\nrestart db.service\nrestart beta.target\nrestart alpha.target",
    )

    result = run_plan(root, change, out)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = read_tsv(out / "plan.tsv")
    by_unit = {r["unit"]: r for r in plan}
    assert int(by_unit["db.service"]["batch"]) < int(by_unit["web.service"]["batch"])
    assert (
        by_unit["shared.service"]["reason"]
        == "propagated:alpha.target;propagated:beta.target"
    )
    assert [r["action"] for r in plan] == ["restart"] * len(plan)


def test_comments_ignored_keys_and_effective_lexical_order(tmp_path: Path) -> None:
    """Semicolon comments and non-planning metadata are ignored, while effective.tsv covers all units lexically."""
    build_planner()
    root = tmp_path / "metadata"
    out = tmp_path / "out"
    write_unit(
        root, "zeta.service", "[Unit]\n;Before=window.target\nPartOf=window.target"
    )
    write_unit(root, "alpha.service", "[Unit]\nDescription=worker\nWants=ghost.service")
    write_unit(
        root, "window.target", "[Unit]\nDocumentation=ignored\nWants=ghost.target"
    )
    write_state(
        root,
        {
            "zeta.service": "active",
            "alpha.service": "inactive",
            "window.target": "active",
        },
    )
    change = tmp_path / "change.txt"
    write_change(change, "restart window.target")

    result = run_plan(root, change, out)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = read_tsv(out / "plan.tsv")
    assert [(r["batch"], r["unit"]) for r in plan] == [
        ("1", "window.target"),
        ("2", "zeta.service"),
    ]
    effective_header, effective_rows = read_tsv_rows(out / "effective.tsv")
    assert effective_header[0] == "unit"
    assert [row[0] for row in effective_rows] == [
        "alpha.service",
        "window.target",
        "zeta.service",
    ]


def test_success_replaces_existing_output_directory(tmp_path: Path) -> None:
    """A successful run atomically replaces a stale output directory with the three current artifacts."""
    build_planner()
    root = tmp_path / "replace"
    write_unit(root, "window.target", "[Unit]")
    write_state(root, {"window.target": "active"})
    change = tmp_path / "change.txt"
    write_change(change, "restart window.target")
    out = tmp_path / "out"
    out.mkdir()
    (out / "plan.tsv").write_text("stale\n", encoding="utf-8")
    (out / "obsolete.tsv").write_text("must disappear\n", encoding="utf-8")

    result = run_plan(root, change, out)
    assert result.returncode == 0, result.stderr + result.stdout
    assert sorted(p.name for p in out.iterdir()) == [
        "effective.tsv",
        "plan.tsv",
        "rollback.tsv",
    ]
    plan = read_tsv(out / "plan.tsv")
    assert [(r["batch"], r["order"], r["unit"], r["reason"]) for r in plan] == [
        ("1", "1", "window.target", "requested")
    ]


def test_hash_comments_ascii_whitespace_and_non_conf_dropins(tmp_path: Path) -> None:
    """Hash comments, tab-separated list values, and non-.conf drop-ins do not change the accepted plan."""
    build_planner()
    root = tmp_path / "metadata_edges"
    write_unit(root, "window.target", "[Unit]")
    write_unit(root, "alpha.service", "[Unit]")
    write_unit(root, "beta.service", "[Unit]")
    write_unit(root, "gamma.service", "[Unit]")
    write_unit(root, "stray.service", "[Unit]\n#PartOf=window.target")
    write_unit(
        root,
        "worker.service",
        "[Unit]\n#After=gamma.service\nPartOf=window.target\nRequires=gamma.service\talpha.service   beta.service",
    )
    write_dropin(
        root, "worker.service", "00-ignore.txt", "[Service]\nRefuseManualRestart=true"
    )
    write_dropin(
        root,
        "worker.service",
        "10-edge.conf",
        "[Unit]\nAfter=alpha.service\tbeta.service",
    )
    write_state(
        root,
        {
            "window.target": "active",
            "alpha.service": "active",
            "beta.service": "active",
            "gamma.service": "active",
            "stray.service": "active",
            "worker.service": "active",
        },
    )
    change = tmp_path / "change.txt"
    write_change(change, "restart window.target")
    out = tmp_path / "out"

    result = run_plan(root, change, out)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = read_tsv(out / "plan.tsv")
    assert [r["unit"] for r in plan] == ["window.target", "worker.service"]
    worker = {r["unit"]: r for r in read_tsv(out / "effective.tsv")}["worker.service"]
    assert worker["requires"] == "alpha.service,beta.service,gamma.service"
    assert worker["after"] == "alpha.service,beta.service"
    assert worker["refuse_manual_restart"] == "false"


def test_invalid_state_table_rejected_without_artifacts(tmp_path: Path) -> None:
    """Bad state.tsv headers and unknown state values reject the dossier before output appears."""
    build_planner()

    for dirname, state_body in [
        ("bad_header", "name\tstate\nwindow.target\tactive\n"),
        ("bad_state", "unit\tstate\nwindow.target\tactivating\n"),
    ]:
        root = tmp_path / dirname
        write_unit(root, "window.target", "[Unit]")
        (root / "state.tsv").write_text(state_body, encoding="utf-8")
        change = root / "change.txt"
        write_change(change, "restart window.target")

        result = run_plan(root, change, root / "out")
        assert result.returncode != 0
        assert not (root / "out").exists()


def test_effective_lists_are_comma_joined_after_lexical_sort(tmp_path: Path) -> None:
    """Multi-value effective.tsv fields are sorted lexically and joined with commas."""
    build_planner()
    root = tmp_path / "effective_lists"
    write_unit(root, "window.target", "[Unit]")
    for name in [
        "alpha.service",
        "beta.service",
        "mu.service",
        "zeta.service",
        "beta.target",
        "zeta.target",
    ]:
        write_unit(root, name, "[Unit]")
    write_unit(
        root,
        "catalog.service",
        "[Unit]\nRequires=zeta.service alpha.service\tmu.service\nAfter=mu.service alpha.service\nBefore=zeta.service beta.service\nPartOf=zeta.target beta.target",
    )
    write_state(
        root,
        {
            "window.target": "active",
            "alpha.service": "active",
            "beta.service": "active",
            "mu.service": "active",
            "zeta.service": "active",
            "beta.target": "inactive",
            "zeta.target": "inactive",
            "catalog.service": "inactive",
        },
    )
    change = tmp_path / "change.txt"
    write_change(change, "restart window.target")
    out = tmp_path / "out"

    result = run_plan(root, change, out)
    assert result.returncode == 0, result.stderr + result.stdout
    effective = {r["unit"]: r for r in read_tsv(out / "effective.tsv")}
    assert (
        effective["catalog.service"]["requires"]
        == "alpha.service,mu.service,zeta.service"
    )
    assert effective["catalog.service"]["after"] == "alpha.service,mu.service"
    assert effective["catalog.service"]["before"] == "beta.service,zeta.service"
    assert effective["catalog.service"]["part_of"] == "beta.target,zeta.target"
