import csv
import math
import subprocess
from pathlib import Path


APP = Path("/app/quad")


def write_config(path, **items):
    path.write_text("\n".join(f"{key}={value}" for key, value in items.items()) + "\n")


def run_solver(config, output, checkpoint):
    return subprocess.run(
        ["./quad_solver", str(config), str(output), str(checkpoint)],
        cwd=APP,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


def kernel(t, alpha, beta, omega):
    if t <= 0.0 or t >= 1.0:
        return 0.0
    x = math.sin(0.5 * math.pi * t) ** 2
    if x <= 0.0 or x >= 1.0:
        return 0.0
    return (x**alpha) * ((1.0 - x) ** beta) * math.cos(omega * x) * (0.5 * math.pi * math.sin(math.pi * t))


def simpson(a, b, fa, fm, fb):
    return (b - a) * (fa + 4.0 * fm + fb) / 6.0


def reference(alpha, beta, omega, tol):
    fa = kernel(0.0, alpha, beta, omega)
    fm = kernel(0.5, alpha, beta, omega)
    fb = kernel(1.0, alpha, beta, omega)
    stack = [(0.0, 1.0, fa, fm, fb, simpson(0.0, 1.0, fa, fm, fb), 0)]
    total = 0.0
    comp = 0.0
    while stack:
        a, b, fa, fm, fb, whole, depth = stack.pop()
        m = 0.5 * (a + b)
        lm = 0.5 * (a + m)
        rm = 0.5 * (m + b)
        flm = kernel(lm, alpha, beta, omega)
        frm = kernel(rm, alpha, beta, omega)
        left = simpson(a, m, fa, flm, fm)
        right = simpson(m, b, fm, frm, fb)
        delta = left + right - whole
        if abs(delta) <= 15.0 * tol * (b - a) or depth >= 64 or (b - a) < 1e-14:
            y = (left + right + delta / 15.0) - comp
            nxt = total + y
            comp = (nxt - total) - y
            total = nxt
        else:
            stack.append((m, b, fm, frm, fb, right, depth + 1))
            stack.append((a, m, fa, flm, fm, left, depth + 1))
    return total


def read_result(path):
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert set(rows[0]) == {"result", "error_estimate", "panels"}
    result = float(rows[0]["result"])
    error = float(rows[0]["error_estimate"])
    panels = int(rows[0]["panels"])
    assert math.isfinite(result)
    assert math.isfinite(error)
    assert error >= 0.0
    assert panels > 0
    return result, error, panels


def test_numerical_contract_a(tmp_path):
    """A completed run reports a stable finite quadrature estimate."""
    subprocess.run(["make", "clean"], cwd=APP, check=True)
    subprocess.run(["make"], cwd=APP, check=True)
    cfg = tmp_path / "a.cfg"
    out = tmp_path / "a.csv"
    chk = tmp_path / "a.chk"
    params = dict(alpha="0.25", beta="0.6", omega="37.0", tol="1e-8", checkpoint_interval="0", panel_limit="0", restart="false")
    write_config(cfg, **params)
    proc = run_solver(cfg, out, chk)
    assert proc.returncode == 0, proc.stderr
    result, error, _ = read_result(out)
    expected = reference(0.25, 0.6, 37.0, 2e-10)
    assert abs(result - expected) <= 7e-8
    assert error < 1e-5


def test_numerical_contract_b(tmp_path):
    """A completed run remains accurate for a difficult valid integral."""
    cfg = tmp_path / "b.cfg"
    out = tmp_path / "b.csv"
    chk = tmp_path / "b.chk"
    params = dict(alpha="-0.37", beta="-0.28", omega="91.5", tol="2e-8", checkpoint_interval="4", panel_limit="0", restart="false")
    write_config(cfg, **params)
    proc = run_solver(cfg, out, chk)
    assert proc.returncode == 0, proc.stderr
    result, error, panels = read_result(out)
    expected = reference(-0.37, -0.28, 91.5, 5e-10)
    assert abs(result - expected) <= 1.5e-7
    assert error < 2e-5
    assert panels >= 16


def test_numerical_contract_c(tmp_path):
    """Restarted and uninterrupted computations give the same reported state."""
    common = dict(alpha="-0.22", beta="1.35", omega="123.0", tol="5e-8", checkpoint_interval="3")
    full_cfg = tmp_path / "full.cfg"
    full_out = tmp_path / "full.csv"
    full_chk = tmp_path / "full.chk"
    write_config(full_cfg, **common, panel_limit="0", restart="false")
    full = run_solver(full_cfg, full_out, full_chk)
    assert full.returncode == 0, full.stderr

    part_cfg = tmp_path / "part.cfg"
    resumed_cfg = tmp_path / "resumed.cfg"
    resumed_out = tmp_path / "resumed.csv"
    chk = tmp_path / "state.chk"
    write_config(part_cfg, **common, panel_limit="7", restart="false")
    partial = run_solver(part_cfg, tmp_path / "partial.csv", chk)
    assert partial.returncode == 3, partial.stderr
    assert chk.exists() and chk.stat().st_size > 0
    write_config(resumed_cfg, **common, panel_limit="0", restart="true")
    resumed = run_solver(resumed_cfg, resumed_out, chk)
    assert resumed.returncode == 0, resumed.stderr
    assert resumed_out.read_bytes() == full_out.read_bytes()


def test_numerical_contract_d(tmp_path):
    """Invalid numerical cases are rejected before a CSV is written."""
    cfg = tmp_path / "d.cfg"
    out = tmp_path / "d.csv"
    chk = tmp_path / "d.chk"
    write_config(cfg, alpha="-0.5", beta="0.2", omega="1.0", tol="1e-8", checkpoint_interval="0", panel_limit="0", restart="false")
    proc = run_solver(cfg, out, chk)
    assert proc.returncode != 0
    assert not out.exists()
