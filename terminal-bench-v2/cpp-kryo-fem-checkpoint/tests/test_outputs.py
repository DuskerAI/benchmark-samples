import json
import math
import struct
import subprocess
from pathlib import Path

APP = Path("/app/kryofem")
BIN = APP / "bin" / "kryofem"
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
WIDTH = 0.5
ORDER = [
    "schema",
    "nodes",
    "steps",
    "checkpoint_step",
    "dx",
    "temperatures",
    "front_position",
    "enthalpy",
    "energy_residual_max",
    "thaw_exposure",
    "checkpoint_digest",
]


def build_binary():
    subprocess.run(
        ["make", "-C", str(APP), "clean", "all"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert BIN.exists(), "the Makefile must build /app/kryofem/bin/kryofem"


def fnv_bytes(data: bytes, h: int = FNV_OFFSET) -> int:
    for b in data:
        h ^= b
        h = (h * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


def add_u64(h: int, value: int) -> int:
    return fnv_bytes(struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF), h)


def add_f64(h: int, value: float) -> int:
    return add_u64(h, struct.unpack("<Q", struct.pack("<d", value))[0])


def config_digest(c: dict) -> int:
    h = FNV_OFFSET
    for key in ["nodes"]:
        h = add_u64(h, int(c[key]))
    for key in [
        "length",
        "dt",
        "conductivity",
        "heat_capacity",
        "latent_heat",
        "melt_temperature",
        "initial_temperature",
        "left_start",
        "right_start",
    ]:
        h = add_f64(h, float(c[key]))
    h = add_u64(h, int(c["checkpoint_step"]))
    for row in c["forcing"][: c["checkpoint_step"]]:
        h = add_u64(h, int(row["step"]))
        h = add_f64(h, float(row["scale"]))
        h = add_f64(h, float(row["left_offset"]))
        h = add_f64(h, float(row["right_offset"]))
        for value in row["source"]:
            h = add_f64(h, float(value))
    return h


def state_digest(
    saved: int, exposure: float, residual: float, temps: list[float]
) -> int:
    h = FNV_OFFSET
    h = add_u64(h, saved)
    h = add_f64(h, exposure)
    h = add_f64(h, residual)
    for temp in temps:
        h = add_f64(h, temp)
    return h


def write_config(path: Path, c: dict):
    forcing_path = path.with_suffix(".forcing")
    write_forcing(forcing_path, c)
    lines = [
        f"nodes={c['nodes']}",
        f"length={c['length']}",
        f"steps={c['steps']}",
        f"dt={c['dt']}",
        f"conductivity={c['conductivity']}",
        f"heat_capacity={c['heat_capacity']}",
        f"latent_heat={c['latent_heat']}",
        f"melt_temperature={c['melt_temperature']}",
        f"initial_temperature={c['initial_temperature']}",
        f"left_start={c['left_start']}",
        f"right_start={c['right_start']}",
        f"left_resume={c['left_resume']}",
        f"right_resume={c['right_resume']}",
        f"checkpoint_step={c['checkpoint_step']}",
        f"resume={'true' if c['resume'] else 'false'}",
        f"forcing_file={forcing_path.name}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_forcing(path: Path, c: dict):
    rows = []
    for row in c["forcing"]:
        values = [
            str(row["step"]),
            repr(row["scale"]),
            repr(row["left_offset"]),
            repr(row["right_offset"]),
            *[repr(v) for v in row["source"]],
        ]
        rows.append(" ".join(values))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def forcing(nodes: int, steps: int, scale_cycle=(1.0, 0.94, 1.08)) -> list[dict]:
    rows = []
    mid = (nodes - 1) / 2.0
    for step in range(1, steps + 1):
        scale = scale_cycle[(step - 1) % len(scale_cycle)]
        left_offset = 0.025 * ((step % 3) - 1)
        right_offset = -0.015 * ((step + 1) % 3)
        source = []
        for i in range(nodes):
            shape = 1.0 - abs(i - mid) / max(mid, 1.0)
            sign = -1.0 if (i + step) % 2 else 1.0
            source.append(sign * 0.018 * step * shape)
        rows.append(
            {
                "step": step,
                "scale": scale,
                "left_offset": left_offset,
                "right_offset": right_offset,
                "source": source,
            }
        )
    return rows


def mass(c: dict):
    dx = c["length"] / (c["nodes"] - 1)
    m = [dx] * c["nodes"]
    m[0] = m[-1] = 0.5 * dx
    return m


def ceff(c: dict, temp: float) -> float:
    return c["heat_capacity"] + (
        c["latent_heat"] if abs(temp - c["melt_temperature"]) <= WIDTH else 0.0
    )


def advance(c: dict, temps: list[float], step: int) -> tuple[list[float], float]:
    n = c["nodes"]
    dx = c["length"] / (n - 1)
    row = c["forcing"][step - 1]
    left_base = c["left_start"] if step <= c["checkpoint_step"] else c["left_resume"]
    right_base = c["right_start"] if step <= c["checkpoint_step"] else c["right_resume"]
    left = row["scale"] * left_base + row["left_offset"]
    right = row["scale"] * right_base + row["right_offset"]
    mm = mass(c)
    lower = [0.0] * n
    diag = [0.0] * n
    upper = [0.0] * n
    rhs = [0.0] * n
    diag[0] = diag[-1] = 1.0
    rhs[0] = left
    rhs[-1] = right
    for i in range(1, n - 1):
        storage = mm[i] * ceff(c, temps[i]) / c["dt"]
        lower[i] = upper[i] = -c["conductivity"] / dx
        diag[i] = storage + 2.0 * c["conductivity"] / dx
        rhs[i] = storage * temps[i] + mm[i] * row["source"][i]
    for i in range(1, n):
        factor = lower[i] / diag[i - 1]
        diag[i] -= factor * upper[i - 1]
        rhs[i] -= factor * rhs[i - 1]
    nxt = [0.0] * n
    nxt[-1] = rhs[-1] / diag[-1]
    for i in range(n - 2, -1, -1):
        nxt[i] = (rhs[i] - upper[i] * nxt[i + 1]) / diag[i]
    residual = 0.0
    for i in range(1, n - 1):
        r = mm[i] * ceff(c, temps[i]) * (nxt[i] - temps[i]) / c["dt"] - c[
            "conductivity"
        ] / dx * (nxt[i - 1] - 2.0 * nxt[i] + nxt[i + 1])
        r -= mm[i] * row["source"][i]
        residual = max(residual, abs(r))
    return nxt, residual


def front_position(c: dict, temps: list[float]) -> float:
    if temps[0] < c["melt_temperature"]:
        return 0.0
    dx = c["length"] / (c["nodes"] - 1)
    for i in range(c["nodes"] - 1):
        if temps[i] >= c["melt_temperature"] and temps[i + 1] < c["melt_temperature"]:
            frac = (c["melt_temperature"] - temps[i]) / (temps[i + 1] - temps[i])
            return (i + frac) * dx
    return c["length"]


def enthalpy(c: dict, temps: list[float]) -> float:
    total = 0.0
    for mm, temp in zip(mass(c), temps):
        liquid = min(
            max((temp - (c["melt_temperature"] - WIDTH)) / (2.0 * WIDTH), 0.0), 1.0
        )
        total += mm * (c["heat_capacity"] * temp + c["latent_heat"] * liquid)
    return total


def reference(
    c: dict, start_step: int = 0, start_temps: list[float] | None = None
) -> dict:
    temps = (
        [c["initial_temperature"]] * c["nodes"]
        if start_temps is None
        else list(start_temps)
    )
    rmax = 0.0
    exposure = 0.0
    for step in range(start_step + 1, c["steps"] + 1):
        temps, residual = advance(c, temps, step)
        rmax = max(rmax, residual)
        exposure += front_position(c, temps) * c["dt"]
    return {
        "temperatures": temps,
        "front_position": front_position(c, temps),
        "enthalpy": enthalpy(c, temps),
        "energy_residual_max": rmax,
        "thaw_exposure": exposure,
    }


def run_case(tmp_path: Path, c: dict, stem: str):
    cfg = tmp_path / f"{stem}.cfg"
    report = tmp_path / f"{stem}.json"
    chk = tmp_path / f"{stem}.bin"
    write_config(cfg, c)
    proc = subprocess.run(
        [str(BIN), str(cfg), str(report), str(chk)],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc, report, chk


def load_report(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert list(data.keys()) == ORDER
    return data


def assert_close_list(actual: list[float], expected: list[float], tol: float = 2e-8):
    assert len(actual) == len(expected)
    for i, (a, e) in enumerate(zip(actual, expected)):
        assert math.isclose(a, e, rel_tol=tol, abs_tol=tol), (
            f"temperature[{i}] {a} != {e}"
        )


def read_checkpoint(path: Path):
    data = path.read_bytes()
    assert data[:8] == b"KRYOFEM2"
    version, nodes, saved, digest, sdigest, exposure, residual = struct.unpack_from(
        "<IIIQQdd", data, 8
    )
    offset = 8 + 4 + 4 + 4 + 8 + 8 + 8 + 8
    temps = list(struct.unpack_from(f"<{nodes}d", data, offset))
    assert offset + nodes * 8 == len(data)
    assert sdigest == state_digest(saved, exposure, residual, temps)
    return version, nodes, saved, digest, exposure, residual, temps


def test_full_run_matches_independent_fem_reference(tmp_path):
    """A full run must match an independent FEM reference and provenance."""
    build_binary()
    c = {
        "nodes": 17,
        "length": 8.0,
        "steps": 9,
        "dt": 0.07,
        "conductivity": 1.35,
        "heat_capacity": 2.1,
        "latent_heat": 6.0,
        "melt_temperature": 0.0,
        "initial_temperature": -1.15,
        "left_start": 1.9,
        "right_start": -1.6,
        "left_resume": 2.6,
        "right_resume": -1.25,
        "checkpoint_step": 4,
        "resume": False,
    }
    c["forcing"] = forcing(c["nodes"], c["steps"])
    proc, report_path, chk_path = run_case(tmp_path, c, "full")
    assert proc.returncode == 0, proc.stderr
    report = load_report(report_path)
    ref = reference(c)
    assert report["schema"] == "kryofem-report-v1"
    assert report["nodes"] == c["nodes"]
    assert report["steps"] == c["steps"]
    assert report["checkpoint_step"] == c["checkpoint_step"]
    assert math.isclose(
        report["dx"], c["length"] / (c["nodes"] - 1), rel_tol=0, abs_tol=5e-12
    )
    assert_close_list(report["temperatures"], ref["temperatures"])
    assert math.isclose(
        report["front_position"], ref["front_position"], rel_tol=2e-8, abs_tol=2e-8
    )
    assert math.isclose(report["enthalpy"], ref["enthalpy"], rel_tol=2e-8, abs_tol=2e-8)
    assert report["energy_residual_max"] <= 2e-9
    assert math.isclose(
        report["thaw_exposure"], ref["thaw_exposure"], rel_tol=2e-8, abs_tol=2e-8
    )
    assert report["checkpoint_digest"] == f"{fnv_bytes(chk_path.read_bytes()):016x}"
    version, nodes, saved, digest, exposure, residual, temps = read_checkpoint(chk_path)
    assert (version, nodes, saved, digest) == (
        1,
        c["nodes"],
        c["checkpoint_step"],
        config_digest(c),
    )
    cp_ref = reference({**c, "steps": c["checkpoint_step"]})
    assert math.isclose(exposure, cp_ref["thaw_exposure"], rel_tol=2e-8, abs_tol=2e-8)
    assert math.isclose(
        residual, cp_ref["energy_residual_max"], rel_tol=2e-8, abs_tol=2e-8
    )
    assert_close_list(temps, cp_ref["temperatures"])


def test_resume_reproduces_direct_state_with_boundary_change(tmp_path):
    """A restart must continue from the saved step like an uninterrupted run."""
    build_binary()
    direct = {
        "nodes": 19,
        "length": 9.5,
        "steps": 11,
        "dt": 0.045,
        "conductivity": 1.05,
        "heat_capacity": 1.7,
        "latent_heat": 4.5,
        "melt_temperature": -0.1,
        "initial_temperature": -1.4,
        "left_start": 1.45,
        "right_start": -1.8,
        "left_resume": 2.2,
        "right_resume": -0.85,
        "checkpoint_step": 5,
        "resume": False,
    }
    direct["forcing"] = forcing(direct["nodes"], direct["steps"])
    proc, direct_report_path, _ = run_case(tmp_path, direct, "direct")
    assert proc.returncode == 0, proc.stderr
    prefix = {
        **direct,
        "steps": direct["checkpoint_step"],
        "resume": False,
        "forcing": direct["forcing"][: direct["checkpoint_step"]],
    }
    proc, prefix_report_path, chk_path = run_case(tmp_path, prefix, "prefix")
    assert proc.returncode == 0, proc.stderr
    assert prefix_report_path.exists()
    resume = {**direct, "resume": True}
    resume_cfg = tmp_path / "resume.cfg"
    resume_report_path = tmp_path / "resume.json"
    write_config(resume_cfg, resume)
    proc = subprocess.run(
        [str(BIN), str(resume_cfg), str(resume_report_path), str(chk_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    direct_report = load_report(direct_report_path)
    resume_report = load_report(resume_report_path)
    for key in [
        "temperatures",
        "front_position",
        "enthalpy",
        "energy_residual_max",
        "thaw_exposure",
    ]:
        if key == "temperatures":
            assert_close_list(resume_report[key], direct_report[key])
        else:
            assert math.isclose(
                resume_report[key], direct_report[key], rel_tol=2e-8, abs_tol=2e-8
            )
    _, nodes, saved, digest, exposure, residual, temps = read_checkpoint(chk_path)
    assert (nodes, saved, digest) == (
        direct["nodes"],
        direct["steps"],
        config_digest(direct),
    )
    assert math.isclose(
        exposure, direct_report["thaw_exposure"], rel_tol=2e-8, abs_tol=2e-8
    )
    assert math.isclose(
        residual, direct_report["energy_residual_max"], rel_tol=2e-8, abs_tol=2e-8
    )
    assert_close_list(temps, direct_report["temperatures"])


def test_restart_rejects_incompatible_and_damaged_checkpoints(tmp_path):
    """Resume rejects incompatible metadata and damaged checkpoint bytes."""
    build_binary()
    base = {
        "nodes": 13,
        "length": 6.0,
        "steps": 4,
        "dt": 0.08,
        "conductivity": 0.9,
        "heat_capacity": 1.8,
        "latent_heat": 3.2,
        "melt_temperature": 0.0,
        "initial_temperature": -0.9,
        "left_start": 1.1,
        "right_start": -1.1,
        "left_resume": 1.7,
        "right_resume": -0.6,
        "checkpoint_step": 4,
        "resume": False,
    }
    base["forcing"] = forcing(base["nodes"], base["steps"])
    proc, _, chk_path = run_case(tmp_path, base, "source")
    assert proc.returncode == 0, proc.stderr
    for mutation in [{"nodes": 14}, {"conductivity": 1.1}, {"checkpoint_step": 3}]:
        bad = {
            **base,
            "steps": 7,
            "resume": True,
            "forcing": forcing(mutation.get("nodes", base["nodes"]), 7),
            **mutation,
        }
        cfg = tmp_path / ("bad_" + "_".join(mutation) + ".cfg")
        report = tmp_path / (cfg.stem + ".json")
        report.write_text("do-not-replace")
        write_config(cfg, bad)
        proc = subprocess.run(
            [str(BIN), str(cfg), str(report), str(chk_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        assert report.read_text() == "do-not-replace"
    for name, payload in [
        ("trailing", chk_path.read_bytes() + b"x"),
        ("mutated", chk_path.read_bytes()[:40] + b"Z" + chk_path.read_bytes()[41:]),
    ]:
        damaged = tmp_path / f"{name}.bin"
        damaged.write_bytes(payload)
        cfg = tmp_path / f"{name}.cfg"
        report = tmp_path / f"{name}.json"
        report.write_text("keep")
        write_config(
            cfg,
            {**base, "steps": 7, "resume": True, "forcing": forcing(base["nodes"], 7)},
        )
        proc = subprocess.run(
            [str(BIN), str(cfg), str(report), str(damaged)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        assert report.read_text() == "keep"


def test_invalid_configs_fail_without_artifact_replacement(tmp_path):
    """Invalid configs fail before requested artifacts are replaced."""
    build_binary()
    valid = {
        "nodes": 9,
        "length": 5.0,
        "steps": 3,
        "dt": 0.05,
        "conductivity": 1.0,
        "heat_capacity": 2.0,
        "latent_heat": 1.5,
        "melt_temperature": 0.0,
        "initial_temperature": -1.0,
        "left_start": 1.0,
        "right_start": -1.0,
        "left_resume": 1.2,
        "right_resume": -0.8,
        "checkpoint_step": 1,
        "resume": False,
    }
    valid["forcing"] = forcing(valid["nodes"], valid["steps"])
    invalid_texts = [
        "nodes=9\nlength=5\nsteps=3\ndt=0.05\nconductivity=1\nheat_capacity=2\nlatent_heat=1\nmelt_temperature=0\ninitial_temperature=-1\nleft_start=1\nright_start=-1\nleft_resume=1\nright_resume=-1\ncheckpoint_step=1\nresume=false\nforcing_file=f.forcing\nextra=1\n",
        "nodes=4\nlength=5\nsteps=3\ndt=0.05\nconductivity=1\nheat_capacity=2\nlatent_heat=1\nmelt_temperature=0\ninitial_temperature=-1\nleft_start=1\nright_start=-1\nleft_resume=1\nright_resume=-1\ncheckpoint_step=1\nresume=false\nforcing_file=f.forcing\n",
        "nodes=9\nlength=5\nsteps=3\ndt=nan\nconductivity=1\nheat_capacity=2\nlatent_heat=1\nmelt_temperature=0\ninitial_temperature=-1\nleft_start=1\nright_start=-1\nleft_resume=1\nright_resume=-1\ncheckpoint_step=1\nresume=false\nforcing_file=f.forcing\n",
        "nodes=9\nlength=5\nsteps=3\ndt=0.05\nconductivity=1\nheat_capacity=2\nlatent_heat=1\nmelt_temperature=0\ninitial_temperature=-1\nleft_start=1\nright_start=-1\nleft_resume=1\nright_resume=-1\ncheckpoint_step=1\nresume=yes\nforcing_file=f.forcing\n",
        "nodes=9\nlength=5\nsteps=3\ndt=0.05\nconductivity=1\nheat_capacity=2\nlatent_heat=1\nmelt_temperature=0\ninitial_temperature=-1\nleft_start=1\nright_start=-1\nleft_resume=1\nright_resume=-1\ncheckpoint_step=1\nresume=false\nforcing_file=missing.forcing\n",
    ]
    for i, text in enumerate(invalid_texts):
        cfg = tmp_path / f"invalid_{i}.cfg"
        report = tmp_path / f"invalid_{i}.json"
        chk = tmp_path / f"invalid_{i}.bin"
        cfg.write_text(text)
        write_forcing(tmp_path / "f.forcing", valid)
        report.write_text("old-report")
        chk.write_text("old-checkpoint")
        proc = subprocess.run(
            [str(BIN), str(cfg), str(report), str(chk)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        assert report.read_text() == "old-report"
        assert chk.read_text() == "old-checkpoint"
    proc, report_path, chk_path = run_case(tmp_path, valid, "valid_after_invalid")
    assert proc.returncode == 0, proc.stderr
    assert load_report(report_path)["nodes"] == valid["nodes"]
    assert chk_path.read_bytes().startswith(b"KRYOFEM2")


def test_zero_step_checkpoint_and_fully_thawed_front(tmp_path):
    """A saved initial state and fully thawed front follow public formulas."""
    build_binary()
    c = {
        "nodes": 11,
        "length": 3.0,
        "steps": 0,
        "dt": 0.1,
        "conductivity": 1.0,
        "heat_capacity": 2.4,
        "latent_heat": 2.0,
        "melt_temperature": -1.0,
        "initial_temperature": 0.25,
        "left_start": 0.25,
        "right_start": 0.25,
        "left_resume": 0.25,
        "right_resume": 0.25,
        "checkpoint_step": 0,
        "resume": False,
    }
    c["forcing"] = []
    proc, report_path, chk_path = run_case(tmp_path, c, "zero")
    assert proc.returncode == 0, proc.stderr
    report = load_report(report_path)
    ref = reference(c)
    assert_close_list(report["temperatures"], ref["temperatures"])
    assert math.isclose(report["front_position"], c["length"], rel_tol=0, abs_tol=1e-12)
    assert math.isclose(report["enthalpy"], ref["enthalpy"], rel_tol=2e-8, abs_tol=2e-8)
    assert report["thaw_exposure"] == 0.0
    assert report["checkpoint_digest"] == f"{fnv_bytes(chk_path.read_bytes()):016x}"
    _, nodes, saved, _, exposure, residual, temps = read_checkpoint(chk_path)
    assert (nodes, saved) == (c["nodes"], 0)
    assert exposure == 0.0
    assert residual == 0.0
    assert_close_list(temps, [c["initial_temperature"]] * c["nodes"])
