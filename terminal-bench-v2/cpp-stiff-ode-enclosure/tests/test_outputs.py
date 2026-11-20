import csv
import math
import subprocess
from pathlib import Path

import pytest

APP = Path("/app/stiffbox")
BIN = APP / "bin/stiffbox"


def build_tool() -> None:
    subprocess.run(["make", "-C", str(APP)], check=True, text=True, capture_output=True)


def write_config(path: Path, **items: object) -> None:
    lines = []
    for key, value in items.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def scalar_config(**overrides: object) -> dict[str, object]:
    items: dict[str, object] = {
        "model": "scalar_relax",
        "t0": "0",
        "t_end": "0.08",
        "sample_dt": "0.04",
        "y0": "1",
        "abs_tol": "0.001",
        "rel_tol": "0",
        "event_index": "0",
        "event_level": "0.5",
        "checkpoint_interval": "0.04",
        "restart_from": "",
        "rate": "3",
        "target": "0",
    }
    items.update(overrides)
    return items


def vector_config(**overrides: object) -> dict[str, object]:
    items: dict[str, object] = {
        "model": "stiff_decay2",
        "t0": "0",
        "t_end": "0.08",
        "sample_dt": "0.04",
        "y0": "0,1",
        "abs_tol": "0.001",
        "rel_tol": "0",
        "event_index": "0",
        "event_level": "0.5",
        "checkpoint_interval": "0.04",
        "restart_from": "",
        "fast": "20",
        "slow": "1",
    }
    items.update(overrides)
    return items


def without(items: dict[str, object], key: str) -> dict[str, object]:
    copy = dict(items)
    del copy[key]
    return copy


def run_case(config: Path, out_dir: Path, stem: str = "run") -> tuple[Path, Path, Path]:
    traj = out_dir / f"{stem}_trajectory.csv"
    events = out_dir / f"{stem}_events.csv"
    checkpoint = out_dir / f"{stem}_checkpoint.bin"
    subprocess.run(
        [str(BIN), str(config), str(traj), str(events), str(checkpoint)],
        check=True,
        text=True,
        capture_output=True,
    )
    return traj, events, checkpoint


def assert_invalid_config(tmp_path: Path, name: str, items: dict[str, object]) -> None:
    cfg = tmp_path / f"{name}.cfg"
    write_config(cfg, **items)
    traj = tmp_path / f"{name}_trajectory.csv"
    events = tmp_path / f"{name}_events.csv"
    checkpoint = tmp_path / f"{name}_checkpoint.bin"
    traj.write_text("stale trajectory\n")
    events.write_text("stale events\n")
    checkpoint.write_bytes(b"stale checkpoint\n")
    try:
        proc = subprocess.run(
            [str(BIN), str(cfg), str(traj), str(events), str(checkpoint)],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError("invalid config was not rejected promptly") from exc
    assert proc.returncode != 0
    assert not traj.exists() and not events.exists() and not checkpoint.exists()


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def first_line(path: Path) -> str:
    return path.read_text(encoding="utf-8").splitlines()[0]


def checkpoint_fields(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "STIFFBOX2"
    return dict(line.split("=", 1) for line in lines[1:] if "=" in line)


def exact_decay2(
    t: float, y0: float, y1: float, fast: float, slow: float
) -> tuple[float, float]:
    ef = math.exp(-fast * t)
    es = math.exp(-slow * t)
    return (y0 - y1) * ef + y1 * es, y1 * es


def exact_scalar(t: float, y0: float, rate: float, target: float) -> float:
    return target + (y0 - target) * math.exp(-rate * t)


def assert_interval(
    row: dict[str, str], idx: int, exact: float, abs_tol: float, rel_tol: float
) -> None:
    lo = float(row[f"y{idx}_lo"])
    hi = float(row[f"y{idx}_hi"])
    assert lo <= exact <= hi, f"component {idx} at t={row['time']} is not enclosed"
    assert hi >= lo
    assert hi - lo <= abs_tol + rel_tol * max(1.0, abs(exact)) + 1e-12


def bisection_for_decay_level(
    level: float, left: float, right: float, fast: float, slow: float
) -> float:
    def f(t: float) -> float:
        return exact_decay2(t, 0.0, 1.0, fast, slow)[0] - level

    fl = f(left)
    for _ in range(80):
        mid = (left + right) / 2.0
        fm = f(mid)
        if fl * fm <= 0:
            right = mid
        else:
            left = mid
            fl = fm
    return (left + right) / 2.0


def test_stiff_vector_trajectory_events_and_checkpoint(tmp_path: Path) -> None:
    """The vector stiff model must produce bounded state intervals, event brackets, and a compatible checkpoint."""
    build_tool()
    cfg = tmp_path / "decay.cfg"
    write_config(
        cfg,
        model="stiff_decay2",
        t0="0",
        t_end="0.18",
        sample_dt="0.03",
        y0="0,1",
        abs_tol="0.0002",
        rel_tol="0.0001",
        event_index="0",
        event_level="0.9",
        checkpoint_interval="0.06",
        restart_from="",
        fast="80",
        slow="1",
    )
    traj, events, checkpoint = run_case(cfg, tmp_path)
    assert first_line(traj) == "time,step,y0_lo,y0_hi,y1_lo,y1_hi,accepted,rejected"
    assert first_line(events) == "event,index,direction,t_lo,t_hi,value_lo,value_hi"
    table = rows(traj)
    assert [round(float(r["time"]), 12) for r in table] == [
        0,
        0.03,
        0.06,
        0.09,
        0.12,
        0.15,
        0.18,
    ]
    for r in table:
        t = float(r["time"])
        e0, e1 = exact_decay2(t, 0.0, 1.0, 80.0, 1.0)
        assert_interval(r, 0, e0, 0.0002, 0.0001)
        assert_interval(r, 1, e1, 0.0002, 0.0001)
        assert int(r["accepted"]) >= 1
        assert int(r["rejected"]) >= 0

    event_rows = rows(events)
    assert [r["direction"] for r in event_rows] == ["up", "down"]
    expected_roots = [
        bisection_for_decay_level(0.9, 0.0, 0.05, 80.0, 1.0),
        bisection_for_decay_level(0.9, 0.08, 0.18, 80.0, 1.0),
    ]
    for r, root in zip(event_rows, expected_roots):
        lo, hi = float(r["t_lo"]), float(r["t_hi"])
        assert lo <= root <= hi
        assert hi - lo <= 0.03 / 8.0 + 1e-8
        assert float(r["value_lo"]) <= 0.9 <= float(r["value_hi"])
    assert checkpoint.read_bytes().startswith(b"STIFFBOX2\n")
    assert math.isclose(
        float(checkpoint_fields(checkpoint)["time"]), 0.18, rel_tol=0, abs_tol=1e-14
    )


def test_restart_matches_uninterrupted_suffix(tmp_path: Path) -> None:
    """A restart from a saved interval time must reproduce the same samples as the full uninterrupted run suffix."""
    build_tool()
    base = {
        "model": "stiff_decay2",
        "t0": "0",
        "sample_dt": "0.025",
        "y0": "0.15,1.25",
        "abs_tol": "0.00015",
        "rel_tol": "0.00012",
        "event_index": "1",
        "event_level": "1.12",
        "checkpoint_interval": "0.05",
        "restart_from": "",
        "fast": "65",
        "slow": "0.75",
    }
    full_cfg = tmp_path / "full.cfg"
    write_config(full_cfg, **base, t_end="0.15")
    full_traj, full_events, _ = run_case(full_cfg, tmp_path, "full")

    partial_cfg = tmp_path / "partial.cfg"
    write_config(partial_cfg, **base, t_end="0.05")
    _, _, partial_ck = run_case(partial_cfg, tmp_path, "partial")

    restart_cfg = tmp_path / "restart.cfg"
    restart_items = dict(base)
    restart_items["t_end"] = "0.15"
    restart_items["checkpoint_interval"] = "0"
    restart_items["restart_from"] = str(partial_ck)
    write_config(restart_cfg, **restart_items)
    restart_traj, restart_events, _ = run_case(restart_cfg, tmp_path, "restart")

    full_suffix = [r for r in rows(full_traj) if float(r["time"]) >= 0.05 - 1e-12]
    restart_rows = rows(restart_traj)
    assert [r["time"] for r in restart_rows] == [r["time"] for r in full_suffix]
    for got, exp in zip(restart_rows, full_suffix):
        for key in ("y0_lo", "y0_hi", "y1_lo", "y1_hi"):
            assert math.isclose(
                float(got[key]), float(exp[key]), rel_tol=0, abs_tol=5e-15
            )
    assert rows(restart_events) == [
        r for r in rows(full_events) if float(r["t_hi"]) >= 0.05 - 1e-12
    ]


def test_scalar_model_and_non_grid_final_time(tmp_path: Path) -> None:
    """The scalar model must include a non-grid final sample and bracket a monotone crossing."""
    build_tool()
    cfg = tmp_path / "scalar.cfg"
    write_config(
        cfg,
        model="scalar_relax",
        t0="0",
        t_end="0.11",
        sample_dt="0.04",
        y0="-1.5",
        abs_tol="0.0001",
        rel_tol="0.0002",
        event_index="0",
        event_level="0.25",
        checkpoint_interval="0",
        restart_from="",
        rate="18",
        target="0.75",
    )
    (tmp_path / "scalar_trajectory.csv").write_text("stale trajectory\n")
    (tmp_path / "scalar_events.csv").write_text("stale events\n")
    (tmp_path / "scalar_checkpoint.bin").write_bytes(b"stale checkpoint\n")
    traj, events, checkpoint = run_case(cfg, tmp_path, "scalar")
    repeat_traj, repeat_events, repeat_checkpoint = run_case(
        cfg, tmp_path, "scalar_repeat"
    )
    assert traj.read_bytes() == repeat_traj.read_bytes()
    assert events.read_bytes() == repeat_events.read_bytes()
    assert checkpoint.read_bytes() == repeat_checkpoint.read_bytes()
    assert first_line(traj) == "time,step,y0_lo,y0_hi,accepted,rejected"
    assert first_line(events) == "event,index,direction,t_lo,t_hi,value_lo,value_hi"
    table = rows(traj)
    assert [round(float(r["time"]), 12) for r in table] == [0, 0.04, 0.08, 0.11]
    for r in table:
        assert_interval(
            r, 0, exact_scalar(float(r["time"]), -1.5, 18.0, 0.75), 0.0001, 0.0002
        )
    event_rows = rows(events)
    assert len(event_rows) == 1 and event_rows[0]["direction"] == "up"
    root = -math.log((0.25 - 0.75) / (-1.5 - 0.75)) / 18.0
    assert float(event_rows[0]["t_lo"]) <= root <= float(event_rows[0]["t_hi"])
    assert math.isclose(
        float(checkpoint_fields(checkpoint)["time"]), 0.11, rel_tol=0, abs_tol=1e-14
    )


def test_comments_and_blank_lines_are_accepted(tmp_path: Path) -> None:
    """Blank lines and whole-line comments in a valid config must be ignored."""
    build_tool()
    cfg = tmp_path / "commented.cfg"
    cfg.write_text(
        "\n"
        "# scalar restart smoke case\n"
        "model=scalar_relax\n"
        "t0=0\n"
        "t_end=0.08\n"
        "\n"
        "sample_dt=0.04\n"
        "y0=1\n"
        "abs_tol=0.001\n"
        "rel_tol=0\n"
        "event_index=0\n"
        "event_level=0.5\n"
        "checkpoint_interval=0.04\n"
        "restart_from=\n"
        "rate=3\n"
        "target=0\n"
        "# end\n"
    )
    traj, _, checkpoint = run_case(cfg, tmp_path, "commented")
    assert [round(float(r["time"]), 12) for r in rows(traj)] == [0, 0.04, 0.08]
    assert checkpoint.read_bytes().startswith(b"STIFFBOX2\n")


def test_cli_requires_documented_argument_count(tmp_path: Path) -> None:
    """The executable must reject invocations other than the documented four path arguments."""
    build_tool()
    proc = subprocess.run([str(BIN)], check=False, text=True, capture_output=True)
    assert proc.returncode != 0
    cfg = tmp_path / "valid.cfg"
    write_config(cfg, **scalar_config())
    proc = subprocess.run(
        [
            str(BIN),
            str(cfg),
            str(tmp_path / "a.csv"),
            str(tmp_path / "b.csv"),
            str(tmp_path / "c.bin"),
            str(tmp_path / "extra.bin"),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0


def test_non_grid_checkpoint_restart_matches_uninterrupted_suffix(
    tmp_path: Path,
) -> None:
    """A non-sample checkpoint must resume on the original sample grid, not a shifted grid."""
    build_tool()
    base = scalar_config(
        t_end="0.18",
        sample_dt="0.04",
        y0="0",
        abs_tol="0.00012",
        rel_tol="0.0001",
        event_level="0.68",
        checkpoint_interval="0.05",
        rate="8",
        target="1",
    )
    full_cfg = tmp_path / "non_grid_full.cfg"
    write_config(full_cfg, **base)
    full_traj, full_events, _ = run_case(full_cfg, tmp_path, "non_grid_full")

    partial_cfg = tmp_path / "non_grid_partial.cfg"
    write_config(partial_cfg, **dict(base, t_end="0.11"))
    _, _, partial_ck = run_case(partial_cfg, tmp_path, "non_grid_partial")
    assert math.isclose(
        float(checkpoint_fields(partial_ck)["time"]), 0.10, rel_tol=0, abs_tol=1e-14
    )

    restart_cfg = tmp_path / "non_grid_restart.cfg"
    write_config(restart_cfg, **dict(base, restart_from=str(partial_ck)))
    restart_traj, restart_events, _ = run_case(
        restart_cfg, tmp_path, "non_grid_restart"
    )

    full_suffix = [r for r in rows(full_traj) if float(r["time"]) >= 0.10 - 1e-12]
    assert rows(restart_traj) == full_suffix
    assert rows(restart_events) == [
        r for r in rows(full_events) if float(r["t_hi"]) >= 0.10 - 1e-12
    ]


@pytest.mark.parametrize(
    ("name", "items"),
    [
        pytest.param(
            "missing_sample_dt",
            without(scalar_config(), "sample_dt"),
            id="missing-required-key",
        ),
        pytest.param("t_end_before_t0", scalar_config(t_end="-0.01"), id="time-range"),
        pytest.param(
            "zero_sample_dt", scalar_config(sample_dt="0"), id="sample-dt-range"
        ),
        pytest.param("zero_abs_tol", scalar_config(abs_tol="0"), id="abs-tol-range"),
        pytest.param(
            "negative_rel_tol", scalar_config(rel_tol="-0.1"), id="rel-tol-range"
        ),
        pytest.param(
            "negative_checkpoint_interval",
            scalar_config(checkpoint_interval="-0.1"),
            id="checkpoint-range",
        ),
        pytest.param("nonfinite_t0", scalar_config(t0="nan"), id="nonfinite-scalar"),
        pytest.param(
            "nonfinite_y0", scalar_config(y0="inf"), id="nonfinite-vector-entry"
        ),
        pytest.param("y0_contains_space", scalar_config(y0="1, 2"), id="y0-spacing"),
        pytest.param(
            "scalar_wrong_dimension", scalar_config(y0="1,2"), id="scalar-dimension"
        ),
        pytest.param(
            "scalar_bad_event_index",
            scalar_config(event_index="1"),
            id="scalar-event-index",
        ),
        pytest.param(
            "unknown_model", scalar_config(model="lorenz"), id="unknown-model"
        ),
        pytest.param("unknown_key", scalar_config(extra="surprise"), id="unknown-key"),
        pytest.param(
            "scalar_missing_target",
            without(scalar_config(), "target"),
            id="scalar-required-key",
        ),
        pytest.param(
            "scalar_extra_vector_key",
            scalar_config(fast="9"),
            id="scalar-model-specific-key",
        ),
        pytest.param(
            "vector_missing_slow",
            without(vector_config(), "slow"),
            id="vector-required-key",
        ),
        pytest.param(
            "vector_wrong_dimension", vector_config(y0="1"), id="vector-dimension"
        ),
        pytest.param(
            "vector_bad_event_index",
            vector_config(event_index="2"),
            id="vector-event-index",
        ),
        pytest.param(
            "vector_bad_rates",
            vector_config(fast="1", slow="1"),
            id="vector-rate-order",
        ),
        pytest.param(
            "vector_extra_scalar_key",
            vector_config(rate="3"),
            id="vector-model-specific-key",
        ),
    ],
)
def test_documented_invalid_configs_leave_no_artifacts(
    tmp_path: Path, name: str, items: dict[str, object]
) -> None:
    """Documented invalid configs must fail nonzero without trajectory, event, or checkpoint artifacts."""
    build_tool()
    assert_invalid_config(tmp_path, name, items)


def test_invalid_configs_and_incompatible_restarts_leave_no_artifacts(
    tmp_path: Path,
) -> None:
    """Malformed configs and checkpoints from a different run must fail without creating report files."""
    build_tool()
    good = tmp_path / "good.cfg"
    write_config(
        good,
        model="scalar_relax",
        t0="0",
        t_end="0.05",
        sample_dt="0.05",
        y0="1",
        abs_tol="0.001",
        rel_tol="0",
        event_index="0",
        event_level="0.5",
        checkpoint_interval="0.05",
        restart_from="",
        rate="4",
        target="0",
    )
    _, _, checkpoint = run_case(good, tmp_path, "good")

    bad = tmp_path / "bad.cfg"
    write_config(
        bad,
        model="scalar_relax",
        t0="0",
        t_end="0.1",
        sample_dt="0.05",
        y0="1",
        abs_tol="0.001",
        rel_tol="0",
        event_index="0",
        event_level="0.5",
        checkpoint_interval="0.05",
        restart_from=str(checkpoint),
        rate="5",
        target="0",
    )
    traj, events, ck = (
        tmp_path / "bad.csv",
        tmp_path / "bad_events.csv",
        tmp_path / "bad.bin",
    )
    proc = subprocess.run(
        [str(BIN), str(bad), str(traj), str(events), str(ck)],
        check=False,
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    assert not traj.exists() and not events.exists() and not ck.exists()

    bad_unknown = tmp_path / "bad_unknown.cfg"
    text = good.read_text() + "extra=surprise\n"
    bad_unknown.write_text(text)
    proc = subprocess.run(
        [str(BIN), str(bad_unknown), str(traj), str(events), str(ck)],
        check=False,
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    assert not traj.exists() and not events.exists() and not ck.exists()
