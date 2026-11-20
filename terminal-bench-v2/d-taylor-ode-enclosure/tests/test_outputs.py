import csv
import math
import subprocess
from pathlib import Path

import pytest


APP = Path("/app/ode")
BIN = APP / "taylor_ode"


@pytest.fixture(scope="session", autouse=True)
def build_solver_once():
    subprocess.run(["make", "clean"], cwd=APP, check=True)
    subprocess.run(["make"], cwd=APP, check=True)
    assert BIN.exists()
    assert BIN.read_bytes().startswith(b"\x7fELF"), "make must produce a native executable"


def write_config(path, **overrides):
    base = {
        "problem": "linear",
        "t0": "0.0",
        "t1": "1.0",
        "y0": "0.5",
        "a": "1.0",
        "b": "0.0",
        "r": "1.0",
        "k": "1.0",
        "event_level": "1.0",
        "tol": "1e-9",
        "h_initial": "0.08",
        "h_min": "0.001",
        "h_max": "0.16",
        "max_order": "9",
        "checkpoint_time": "1.0",
        "restart": "false",
    }
    base.update({k: str(v) for k, v in overrides.items()})
    path.write_text("\n".join(f"{k}={base[k]}" for k in base) + "\n")


def run_case(tmp_path, name, **cfg):
    cpath = tmp_path / f"{name}.cfg"
    traj = tmp_path / f"{name}.traj.csv"
    events = tmp_path / f"{name}.events.csv"
    chk = tmp_path / f"{name}.chk"
    write_config(cpath, **cfg)
    subprocess.run([str(BIN), str(cpath), str(traj), str(events), str(chk)], cwd=APP, check=True)
    return cpath, traj, events, chk


def rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def linear_value(t, *, t0, y0, a, b, **_unused):
    if abs(a) < 1e-15:
        return y0 + b * (t - t0)
    return (y0 + b / a) * math.exp(a * (t - t0)) - b / a


def logistic_value(t, *, t0, y0, r, k, **_unused):
    q = (k - y0) / y0
    return k / (1.0 + q * math.exp(-r * (t - t0)))


def forced_rhs(t, y, *, r, k, a, b, **_unused):
    return r * y * (1.0 - y / k) + a * math.cos(b * t)


def rk4_step(t, y, h, *, r, k, a, b):
    k1 = forced_rhs(t, y, r=r, k=k, a=a, b=b)
    k2 = forced_rhs(t + h / 2.0, y + h * k1 / 2.0, r=r, k=k, a=a, b=b)
    k3 = forced_rhs(t + h / 2.0, y + h * k2 / 2.0, r=r, k=k, a=a, b=b)
    k4 = forced_rhs(t + h, y + h * k3, r=r, k=k, a=a, b=b)
    return y + h * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0


def forced_logistic_value(t, *, t0, y0, r, k, a, b, atol=2e-12, rtol=2e-12, **_unused):
    """Independent adaptive step-doubling reference for the nonautonomous ODE."""
    y = y0
    x = t0
    direction = 1.0 if t >= t0 else -1.0
    h_abs = min(0.025, max(abs(t - t0) / 25.0, 1e-4))
    while direction * (t - x) > 1e-15:
        h = direction * min(h_abs, abs(t - x))
        full = rk4_step(x, y, h, r=r, k=k, a=a, b=b)
        half = rk4_step(x, y, h / 2.0, r=r, k=k, a=a, b=b)
        refined = rk4_step(x + h / 2.0, half, h / 2.0, r=r, k=k, a=a, b=b)
        err = abs(refined - full) / 15.0
        limit = atol + rtol * max(1.0, abs(refined))
        if err <= limit or abs(h) <= 1e-7:
            y = refined + (refined - full) / 15.0
            x += h
            if err < limit / 32.0:
                h_abs = min(0.05, h_abs * 2.0)
        else:
            h_abs = max(1e-7, h_abs * max(0.25, 0.8 * (limit / err) ** 0.2))
    return y


def linear_event_time(level, *, t0, y0, a, b, **_unused):
    if abs(a) < 1e-15:
        return t0 + (level - y0) / b
    return t0 + math.log((level + b / a) / (y0 + b / a)) / a


def logistic_event_time(level, *, t0, y0, r, k, **_unused):
    return t0 - math.log((k / level - 1.0) / ((k - y0) / y0)) / r


def forced_event_time(level, *, t0, t1, y0, r, k, a, b, **_unused):
    scan_step = min(0.01, max((t1 - t0) / 500.0, 1e-3))
    left_t = t0
    left_g = y0 - level
    while left_t < t1 - 1e-15:
        next_t = min(left_t + scan_step, t1)
        next_g = forced_logistic_value(next_t, t0=t0, y0=y0, r=r, k=k, a=a, b=b) - level
        if left_g == 0.0:
            return left_t
        if left_g * next_g <= 0.0:
            lo, hi = left_t, next_t
            glo = left_g
            for _ in range(70):
                mid = (lo + hi) / 2.0
                mid_g = forced_logistic_value(mid, t0=t0, y0=y0, r=r, k=k, a=a, b=b) - level
                if glo * mid_g <= 0.0:
                    hi = mid
                else:
                    lo = mid
                    glo = mid_g
            return (lo + hi) / 2.0
        left_t, left_g = next_t, next_g
    raise AssertionError("forced logistic event level was not crossed")


def assert_interval_contains(row, prefix, value, slack=0.0):
    lo = float(row[f"{prefix}_lo"])
    hi = float(row[f"{prefix}_hi"])
    assert lo <= hi
    assert lo - slack <= value <= hi + slack
    return lo, hi


def assert_trajectory_contract(path, out, *, t1, h_min, h_max, max_order, tol):
    assert path.read_text().splitlines()[0] == "step,t_lo,t_hi,y_lo,y_hi,order,h"
    assert out[0]["step"] == "0"
    assert int(out[0]["order"]) == 0
    assert float(out[0]["h"]) == 0.0
    previous_hi = -math.inf
    previous_mid = None
    for expected_step, row in enumerate(out):
        assert int(row["step"]) == expected_step
        tlo, thi = float(row["t_lo"]), float(row["t_hi"])
        assert tlo <= thi
        assert tlo >= previous_hi - 1e-14
        previous_hi = thi
        ylo, yhi = float(row["y_lo"]), float(row["y_hi"])
        assert ylo <= yhi
        assert yhi - ylo <= max(2e-6, 10 * tol) + 1e-14
        if expected_step > 0:
            assert previous_mid is not None
            assert math.isclose(((tlo + thi) / 2.0) - previous_mid, float(row["h"]), rel_tol=2e-12, abs_tol=5e-13)
            if expected_step < len(out) - 1:
                assert h_min - 1e-15 <= float(row["h"])
            else:
                assert float(row["h"]) > 0.0
            assert float(row["h"]) <= h_max + 1e-15
            assert 1 <= int(row["order"]) <= max_order
        previous_mid = (tlo + thi) / 2.0
    assert float(out[-1]["t_lo"]) <= t1 <= float(out[-1]["t_hi"])


def assert_event_contract(path, ev, *, tol):
    assert path.read_text().splitlines()[0] == "index,t_lo,t_hi,y_lo,y_hi"
    previous_hi = -math.inf
    for expected_index, row in enumerate(ev):
        assert int(row["index"]) == expected_index
        tlo, thi = float(row["t_lo"]), float(row["t_hi"])
        assert tlo <= thi
        assert tlo >= previous_hi - 1e-14
        assert thi - tlo <= max(tol, 1e-12) + 1e-18
        previous_hi = thi


def test_linear_enclosures_and_event_brackets(tmp_path):
    """Linear output uses exact CSV schemas, bounded steps, enclosures, and a tight event bracket."""
    params = dict(t0=0.0, t1=1.15, y0=0.42, a=1.65, b=-0.08)
    level = 1.07
    _, traj, events, _ = run_case(
        tmp_path,
        "linear",
        problem="linear",
        event_level=level,
        tol="2e-9",
        h_initial="0.13",
        h_min="0.002",
        h_max="0.17",
        max_order="10",
        checkpoint_time="1.15",
        **params,
    )
    out = rows(traj)
    assert_trajectory_contract(traj, out, t1=params["t1"], h_min=0.002, h_max=0.17, max_order=10, tol=2e-9)
    for row in out:
        tlo, thi = assert_interval_contains(row, "t", (float(row["t_lo"]) + float(row["t_hi"])) / 2.0)
        tm = (tlo + thi) / 2.0
        ref = linear_value(tm, **params)
        assert_interval_contains(row, "y", ref, slack=2e-12)
    ev = rows(events)
    assert_event_contract(events, ev, tol=2e-9)
    assert len(ev) == 1
    root = linear_event_time(level, **params)
    tlo, thi = assert_interval_contains(ev[0], "t", root, slack=2e-12)
    assert thi - tlo <= max(2e-9, 1e-12)
    assert_interval_contains(ev[0], "y", level, slack=2e-12)


def test_logistic_near_terminal_event_isolated(tmp_path):
    """Logistic output encloses sampled states and isolates a slowly changing terminal crossing."""
    params = dict(t0=0.0, t1=4.8, y0=0.19, r=1.35, k=1.0)
    level = 0.928
    _, traj, events, _ = run_case(
        tmp_path,
        "logistic",
        problem="logistic",
        event_level=level,
        tol="6e-10",
        h_initial="0.31",
        h_min="0.0005",
        h_max="0.42",
        max_order="11",
        checkpoint_time="4.8",
        a="0.0",
        b="0.0",
        **params,
    )
    out = rows(traj)
    assert_trajectory_contract(traj, out, t1=params["t1"], h_min=0.0005, h_max=0.42, max_order=11, tol=6e-10)
    for row in out[:: max(1, len(out) // 9)]:
        tm = (float(row["t_lo"]) + float(row["t_hi"])) / 2.0
        assert_interval_contains(row, "y", logistic_value(tm, **params), slack=2e-12)
    ev = rows(events)
    assert_event_contract(events, ev, tol=6e-10)
    assert len(ev) == 1
    root = logistic_event_time(level, **params)
    tlo, thi = assert_interval_contains(ev[0], "t", root, slack=2e-12)
    assert thi - tlo <= max(6e-10, 1e-12)
    assert_interval_contains(ev[0], "y", level, slack=2e-12)


@pytest.mark.parametrize(
    "name,params,level,tol,h_initial,h_min,h_max,max_order",
    [
        pytest.param(
            "near_carrying_capacity",
            dict(t0=0.0, t1=5.9, y0=0.11, r=1.05, k=1.0),
            0.973,
            "4e-10",
            "0.29",
            "0.0004",
            "0.47",
            "12",
            id="near_carrying_capacity",
        ),
        pytest.param(
            "descending_late",
            dict(t0=0.0, t1=3.7, y0=0.94, r=-1.25, k=1.0),
            0.21,
            "7e-10",
            "0.23",
            "0.0006",
            "0.36",
            "13",
            id="descending_late",
        ),
    ],
)
def test_logistic_event_rows_are_unique_and_width_safe(tmp_path, name, params, level, tol, h_initial, h_min, h_max, max_order):
    """Monotone logistic cases produce one parsed-width-safe row for the actual crossing."""
    _, traj, events, _ = run_case(
        tmp_path,
        name,
        problem="logistic",
        event_level=level,
        tol=tol,
        h_initial=h_initial,
        h_min=h_min,
        h_max=h_max,
        max_order=max_order,
        checkpoint_time=params["t1"],
        a="0.0",
        b="0.0",
        **params,
    )
    out = rows(traj)
    assert_trajectory_contract(traj, out, t1=params["t1"], h_min=float(h_min), h_max=float(h_max), max_order=int(max_order), tol=float(tol))
    for row in out[:: max(1, len(out) // 7)]:
        tm = (float(row["t_lo"]) + float(row["t_hi"])) / 2.0
        assert_interval_contains(row, "y", logistic_value(tm, **params), slack=2e-12)
    ev = rows(events)
    assert_event_contract(events, ev, tol=float(tol))
    assert len(ev) == 1
    root = logistic_event_time(level, **params)
    tlo, thi = assert_interval_contains(ev[0], "t", root, slack=2e-12)
    assert thi - tlo <= max(float(tol), 1e-12)
    assert_interval_contains(ev[0], "y", level, slack=2e-12)


def test_descending_logistic_crossing_and_empty_event_file(tmp_path):
    """Descending logistic crossings and no-crossing runs both keep the event schema precise."""
    params = dict(t0=0.0, t1=2.55, y0=0.82, r=-0.95, k=1.0)
    level = 0.35
    _, traj, events, _ = run_case(
        tmp_path,
        "descending",
        problem="logistic",
        event_level=level,
        tol="8e-10",
        h_initial="0.27",
        h_min="0.0007",
        h_max="0.31",
        max_order="12",
        checkpoint_time="2.55",
        a="0.0",
        b="0.0",
        **params,
    )
    out = rows(traj)
    assert_trajectory_contract(traj, out, t1=params["t1"], h_min=0.0007, h_max=0.31, max_order=12, tol=8e-10)
    for row in out[:: max(1, len(out) // 8)]:
        tm = (float(row["t_lo"]) + float(row["t_hi"])) / 2.0
        assert_interval_contains(row, "y", logistic_value(tm, **params), slack=2e-12)
    ev = rows(events)
    assert_event_contract(events, ev, tol=8e-10)
    assert len(ev) == 1
    root = logistic_event_time(level, **params)
    tlo, thi = assert_interval_contains(ev[0], "t", root, slack=2e-12)
    assert thi - tlo <= max(8e-10, 1e-12)
    assert_interval_contains(ev[0], "y", level, slack=2e-12)

    _, quiet_traj, quiet_events, _ = run_case(
        tmp_path,
        "quiet",
        problem="linear",
        t0="0.0",
        t1="0.55",
        y0="0.25",
        a="0.0",
        b="0.0",
        event_level="1.4",
        tol="5e-10",
        h_initial="2.5",
        h_min="0.01",
        h_max="0.11",
        max_order="8",
        checkpoint_time="0.0",
    )
    assert quiet_events.read_text() == "index,t_lo,t_hi,y_lo,y_hi\n"
    quiet_rows = rows(quiet_traj)
    assert_trajectory_contract(quiet_traj, quiet_rows, t1=0.55, h_min=0.01, h_max=0.11, max_order=8, tol=5e-10)


def test_forced_logistic_nonautonomous_enclosure_and_resume(tmp_path):
    """The nonautonomous logistic case needs tight enclosures, event isolation, and restart replay."""
    params = dict(t0=0.0, t1=2.4, y0=0.22, r=0.75, k=1.3, a=0.18, b=2.4)
    common = dict(
        problem="forced_logistic",
        event_level="0.55",
        tol="8e-8",
        h_initial="0.34",
        h_min="0.0002",
        h_max="0.19",
        max_order="12",
        **params,
    )
    _, full_traj, full_events, _ = run_case(tmp_path, "forced_full", checkpoint_time="2.4", restart="false", **common)
    _, prefix_traj, _, chk = run_case(tmp_path, "forced_prefix", checkpoint_time="0.83", restart="false", **common)
    restart_cfg = tmp_path / "forced_restart.cfg"
    restart_traj = tmp_path / "forced_restart.traj.csv"
    restart_events = tmp_path / "forced_restart.events.csv"
    write_config(restart_cfg, checkpoint_time="0.83", restart="true", **common)
    subprocess.run([str(BIN), str(restart_cfg), str(restart_traj), str(restart_events), str(chk)], cwd=APP, check=True)

    out = rows(full_traj)
    assert_trajectory_contract(full_traj, out, t1=params["t1"], h_min=0.0002, h_max=0.19, max_order=12, tol=8e-8)
    assert prefix_traj.read_text().count("\n") < full_traj.read_text().count("\n")
    assert restart_traj.read_bytes() == full_traj.read_bytes()
    assert restart_events.read_bytes() == full_events.read_bytes()
    for row in out[:: max(1, len(out) // 8)]:
        tm = (float(row["t_lo"]) + float(row["t_hi"])) / 2.0
        ylo, yhi = assert_interval_contains(row, "y", forced_logistic_value(tm, **params), slack=8e-8)
        assert yhi - ylo <= max(2e-6, 10 * 8e-8)
    ev = rows(full_events)
    assert_event_contract(full_events, ev, tol=8e-8)
    assert len(ev) == 1
    root = forced_event_time(0.55, **params)
    tlo, thi = assert_interval_contains(ev[0], "t", root, slack=8e-8)
    assert thi - tlo <= max(8e-8, 1e-12)
    assert_interval_contains(ev[0], "y", 0.55, slack=8e-8)


def test_checkpoint_resume_matches_uninterrupted_outputs(tmp_path):
    """Checkpoint resume writes standalone final CSVs from t0 that match uninterrupted bytes exactly."""
    common = dict(
        problem="linear",
        t0="0.0",
        t1="1.4",
        y0="0.73",
        a="-0.55",
        b="1.2",
        event_level="1.23",
        tol="1e-9",
        h_initial="0.19",
        h_min="0.001",
        h_max="0.21",
        max_order="10",
    )
    _, full_traj, full_events, _ = run_case(tmp_path, "full", checkpoint_time="1.4", restart="false", **common)
    first_cfg, first_traj, first_events, chk = run_case(tmp_path, "prefix", checkpoint_time="0.57", restart="false", **common)
    restart_cfg = tmp_path / "restart.cfg"
    restart_traj = tmp_path / "restart.traj.csv"
    restart_events = tmp_path / "restart.events.csv"
    write_config(restart_cfg, checkpoint_time="0.57", restart="true", **common)
    subprocess.run([str(BIN), str(restart_cfg), str(restart_traj), str(restart_events), str(chk)], cwd=APP, check=True)
    assert first_traj.read_text().count("\n") < full_traj.read_text().count("\n")
    assert first_events.read_text().splitlines()[0] == "index,t_lo,t_hi,y_lo,y_hi"
    assert restart_traj.read_bytes() == full_traj.read_bytes()
    assert restart_events.read_bytes() == full_events.read_bytes()


def test_restart_rejects_stale_and_future_checkpoints(tmp_path):
    """Restart mode rejects same-run prefixes from before or after the requested checkpoint boundary."""
    common = dict(
        problem="linear",
        t0="0.0",
        t1="1.2",
        y0="0.39",
        a="0.72",
        b="0.18",
        event_level="0.82",
        tol="1e-9",
        h_initial="0.13",
        h_min="0.001",
        h_max="0.15",
        max_order="10",
    )
    _, _, _, early_chk = run_case(tmp_path, "early_prefix", checkpoint_time="0.32", restart="false", **common)
    stale_cfg = tmp_path / "stale.cfg"
    write_config(stale_cfg, checkpoint_time="0.74", restart="true", **common)
    stale = subprocess.run(
        [str(BIN), str(stale_cfg), str(tmp_path / "stale.traj.csv"), str(tmp_path / "stale.events.csv"), str(early_chk)],
        cwd=APP,
        capture_output=True,
        text=True,
    )
    assert stale.returncode != 0

    _, _, _, late_chk = run_case(tmp_path, "late_prefix", checkpoint_time="0.74", restart="false", **common)
    future_cfg = tmp_path / "future.cfg"
    write_config(future_cfg, checkpoint_time="0.32", restart="true", **common)
    future = subprocess.run(
        [str(BIN), str(future_cfg), str(tmp_path / "future.traj.csv"), str(tmp_path / "future.events.csv"), str(late_chk)],
        cwd=APP,
        capture_output=True,
        text=True,
    )
    assert future.returncode != 0


def test_restart_rejects_missing_corrupt_and_incompatible_checkpoints(tmp_path):
    """Restart mode rejects absent, malformed, cross-configuration, and payload-corrupted checkpoints."""
    common = dict(
        problem="linear",
        t0="0.0",
        t1="0.9",
        y0="0.48",
        a="0.85",
        b="0.1",
        event_level="0.9",
        tol="1e-9",
        h_initial="0.14",
        h_min="0.001",
        h_max="0.16",
        max_order="10",
    )
    missing_cfg = tmp_path / "missing.cfg"
    write_config(missing_cfg, checkpoint_time="0.3", restart="true", **common)
    missing = subprocess.run(
        [str(BIN), str(missing_cfg), str(tmp_path / "missing.traj.csv"), str(tmp_path / "missing.events.csv"), str(tmp_path / "absent.chk")],
        cwd=APP,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0

    corrupt = tmp_path / "corrupt.chk"
    corrupt.write_text("not a valid checkpoint\n")
    corrupt_cfg = tmp_path / "corrupt.cfg"
    write_config(corrupt_cfg, checkpoint_time="0.3", restart="true", **common)
    corrupt_run = subprocess.run(
        [str(BIN), str(corrupt_cfg), str(tmp_path / "corrupt.traj.csv"), str(tmp_path / "corrupt.events.csv"), str(corrupt)],
        cwd=APP,
        capture_output=True,
        text=True,
    )
    assert corrupt_run.returncode != 0

    _, _, _, chk = run_case(tmp_path, "base", checkpoint_time="0.35", restart="false", **common)
    incompatible_cfg = tmp_path / "incompatible.cfg"
    write_config(incompatible_cfg, checkpoint_time="0.35", restart="true", y0="0.51", **{k: v for k, v in common.items() if k != "y0"})
    incompatible = subprocess.run(
        [str(BIN), str(incompatible_cfg), str(tmp_path / "bad.traj.csv"), str(tmp_path / "bad.events.csv"), str(chk)],
        cwd=APP,
        capture_output=True,
        text=True,
    )
    assert incompatible.returncode != 0

    payload_corrupt = tmp_path / "payload-corrupt.chk"
    original = chk.read_bytes()
    assert len(original) > 12
    damaged = original[: max(1, len(original) // 3)]
    payload_corrupt.write_bytes(damaged)
    payload_cfg = tmp_path / "payload.cfg"
    write_config(payload_cfg, checkpoint_time="0.35", restart="true", **common)
    payload_run = subprocess.run(
        [str(BIN), str(payload_cfg), str(tmp_path / "payload.traj.csv"), str(tmp_path / "payload.events.csv"), str(payload_corrupt)],
        cwd=APP,
        capture_output=True,
        text=True,
    )
    assert payload_run.returncode != 0
    assert payload_corrupt.read_bytes() == damaged
