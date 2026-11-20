import json
import math
import subprocess
from pathlib import Path

APP = Path("/app")
BIN = APP / "bin" / "amrcert"


def build() -> None:
    subprocess.run(["make", "-C", str(APP)], check=True, capture_output=True, text=True)


def write_case(path: Path, values: tuple) -> None:
    nx, t_end, cfl, disc, left, right, max_level, sensor, boundary = values
    path.write_text(
        f"{nx} {t_end:.12f} {cfl:.12f} {disc:.12f} {left:.12f} {right:.12f} {max_level} {sensor:.12f} {boundary}\n",
        encoding="utf-8",
    )


def run_case(case_path: Path, out_path: Path, *extra: str) -> bytes:
    subprocess.run([str(BIN), str(case_path), str(out_path), *extra], check=True, capture_output=True, text=True)
    return out_path.read_bytes()


def initial_mass(nx: int, disc: float, left: float, right: float) -> float:
    dx = 1.0 / nx
    total = 0.0
    for i in range(nx):
        mid = (i + 0.5) * dx
        total += dx * (left if mid < disc else right)
    return total


def load_report(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def check_report(report: dict, values: tuple) -> None:
    nx, t_end, _cfl, disc, left, right, max_level, _sensor, boundary = values
    assert set(report) == {"status", "case", "summary", "cells"}
    assert report["status"] == "ok"
    summary = report["summary"]
    cells = report["cells"]
    assert set(summary) == {"time", "steps", "cell_count", "mass", "min", "max", "total_variation", "mesh_error", "mass_error"}
    assert report["case"]["nx"] == nx
    assert report["case"]["boundary"] == boundary
    assert report["case"]["max_level"] == max_level
    assert summary["cell_count"] == len(cells)
    assert summary["steps"] >= 0
    assert math.isclose(summary["time"], t_end, rel_tol=0.0, abs_tol=5e-12)
    assert summary["mesh_error"] <= 5e-12
    assert cells[0]["lo"] == 0.0
    assert cells[-1]["hi"] == 1.0
    mass = 0.0
    prev_hi = 0.0
    lo_bound = min(left, right) - 2e-10
    hi_bound = max(left, right) + 2e-10
    seen_level = False
    for cell in cells:
        assert set(cell) == {"lo", "hi", "level", "u"}
        assert math.isclose(cell["lo"], prev_hi, rel_tol=0.0, abs_tol=5e-12)
        assert cell["hi"] > cell["lo"]
        assert 0 <= cell["level"] <= max_level
        assert math.isfinite(cell["u"])
        assert lo_bound <= cell["u"] <= hi_bound
        width = cell["hi"] - cell["lo"]
        assert math.isclose(width * nx * (2 ** cell["level"]), 1.0, rel_tol=2e-9, abs_tol=2e-9)
        mass += width * cell["u"]
        prev_hi = cell["hi"]
        seen_level = seen_level or cell["level"] > 0
    assert math.isclose(mass, summary["mass"], rel_tol=0.0, abs_tol=5e-11)
    assert math.isclose(summary["min"], min(c["u"] for c in cells), rel_tol=0.0, abs_tol=5e-11)
    assert math.isclose(summary["max"], max(c["u"] for c in cells), rel_tol=0.0, abs_tol=5e-11)
    assert math.isfinite(summary["total_variation"])
    assert len(cells) <= nx * (2 ** max_level)
    if left != right and max_level > 0:
        assert seen_level
    if boundary == "periodic":
        target = initial_mass(nx, disc, left, right)
        assert math.isclose(mass, target, rel_tol=0.0, abs_tol=2e-9)
        assert abs(summary["mass_error"]) <= 2e-9


def test_generated_certificates(tmp_path: Path):
    """Generated shock-tube certificates satisfy the published invariants."""
    build()
    cases = [
        (36, 0.080000000000, 0.42, 0.410000000000, 1.7, 0.2, 3, 0.070000000000, "periodic"),
        (48, 0.055000000000, 0.38, 0.630000000000, -0.6, 0.9, 3, 0.060000000000, "periodic"),
        (40, 0.100000000000, 0.37, 0.500000000000, 0.37, 0.37, 2, 0.050000000000, "outflow"),
    ]
    for idx, values in enumerate(cases):
        case_path = tmp_path / f"case_{idx}.txt"
        out_path = tmp_path / f"out_{idx}.json"
        write_case(case_path, values)
        first = run_case(case_path, out_path)
        second_path = tmp_path / f"out_{idx}_repeat.json"
        second = run_case(case_path, second_path)
        assert first == second
        check_report(load_report(out_path), values)


def test_restart_matches_uninterrupted_run(tmp_path: Path):
    """Checkpoint and resume produce the same scientific certificate as a continuous run."""
    build()
    values = (44, 0.073000000000, 0.41, 0.370000000000, 1.45, -0.25, 3, 0.050000000000, "periodic")
    case_path = tmp_path / "case.txt"
    write_case(case_path, values)
    full_path = tmp_path / "full.json"
    checkpointed_path = tmp_path / "checkpointed.json"
    resumed_path = tmp_path / "resumed.json"
    checkpoint_path = tmp_path / "state.chk"
    full = run_case(case_path, full_path)
    checkpointed = run_case(case_path, checkpointed_path, "--checkpoint", str(checkpoint_path), "--checkpoint-every", "4")
    assert checkpoint_path.exists() and checkpoint_path.stat().st_size > 0
    resumed = run_case(case_path, resumed_path, "--resume", str(checkpoint_path))
    assert checkpointed == full
    assert resumed == full
    check_report(load_report(resumed_path), values)
