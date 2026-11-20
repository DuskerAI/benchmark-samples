import hashlib
import json
import math
import os
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np

DRIVER = Path("/app/task_file/femdiff.jl")


def write_config(path: Path, **overrides) -> dict:
    cfg = {
        "nodes": 23,
        "steps": 10,
        "dt": 0.037,
        "diffusion": 0.71,
        "left_bc": 1.35,
        "right_flux": -0.42,
        "source": 0.18,
        "source_slope": 0.0,
        "source_wave": 0.0,
        "initial_offset": -0.12,
        "initial_slope": 0.33,
        "initial_wave": 0.27,
        "checkpoint_interval": 3,
        "restart": False,
        "ordering": "natural",
        "mesh_power": 1.0,
    }
    cfg.update(overrides)
    lines = []
    for key, value in cfg.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, str):
            rendered = json.dumps(value)
        else:
            rendered = repr(value)
        lines.append(f"{key} = {rendered}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg


def step_time(cfg: dict, step: int) -> float:
    dt = float(cfg["dt"])
    growth = float(cfg.get("dt_growth", 0.0))
    return dt * (step + growth * step * (step - 1) / 2.0)


def run_driver(config: Path, output: Path, checkpoint: Path) -> dict:
    result = subprocess.run(
        [
            "julia",
            "--project=/app/task_file",
            str(DRIVER),
            str(config),
            str(output),
            str(checkpoint),
        ],
        cwd="/app",
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.exists(), f"missing output file {output}"
    return json.loads(output.read_text(encoding="utf-8"))


def reference_solution(cfg: dict, start_step: int = 0, start_values=None) -> tuple[np.ndarray, np.ndarray, list[float]]:
    n = int(cfg["nodes"])
    mesh_power = float(cfg.get("mesh_power", 1.0))
    nodes = np.linspace(0.0, 1.0, n) ** mesh_power

    def assemble_at(time_value: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        mass = np.zeros((n, n), dtype=float)
        stiff = np.zeros((n, n), dtype=float)
        react = np.zeros((n, n), dtype=float)
        load = np.zeros(n, dtype=float)
        gauss_xi = np.array([-math.sqrt(3.0 / 5.0), 0.0, math.sqrt(3.0 / 5.0)])
        gauss_w = np.array([5.0 / 9.0, 8.0 / 9.0, 5.0 / 9.0])
        diffusion = float(cfg["diffusion"])
        diffusion_slope = float(cfg.get("diffusion_slope", 0.0))
        diffusion_time = float(cfg.get("diffusion_time", 0.0))
        reaction = float(cfg.get("reaction", 0.0))
        reaction_slope = float(cfg.get("reaction_slope", 0.0))
        reaction_time = float(cfg.get("reaction_time", 0.0))
        for e in range(n - 1):
            h = nodes[e + 1] - nodes[e]
            lm = (h / 6.0) * np.array([[2.0, 1.0], [1.0, 2.0]])
            lk = np.zeros((2, 2), dtype=float)
            lr = np.zeros((2, 2), dtype=float)
            lf = np.zeros(2, dtype=float)
            for xi, weight in zip(gauss_xi, gauss_w):
                x = 0.5 * (nodes[e] + nodes[e + 1]) + 0.5 * h * xi
                phi = np.array([(nodes[e + 1] - x) / h, (x - nodes[e]) / h])
                grad = np.array([-1.0 / h, 1.0 / h])
                interface_x = min(max(float(cfg.get("interface_x", 1.0)), 0.0), 1.0)
                interface_contrast = float(cfg.get("interface_contrast", 1.0))
                coeff = diffusion * (1.0 + diffusion_slope * x) * (1.0 + diffusion_time * time_value)
                if x >= interface_x:
                    coeff *= interface_contrast
                react_coeff = reaction * (1.0 + reaction_slope * x) * (1.0 + reaction_time * time_value)
                q = (
                    float(cfg["source"])
                    + float(cfg.get("source_slope", 0.0)) * x
                    + float(cfg.get("source_quadratic", 0.0)) * x * x
                    + float(cfg.get("source_wave", 0.0)) * math.sin(2.0 * math.pi * x)
                    + time_value * float(cfg.get("source_time", 0.0))
                )
                lk += weight * 0.5 * h * coeff * np.outer(grad, grad)
                lr += weight * 0.5 * h * react_coeff * np.outer(phi, phi)
                lf += weight * 0.5 * h * q * phi
            ids = [e, e + 1]
            for a in range(2):
                load[ids[a]] += lf[a]
                for b in range(2):
                    mass[ids[a], ids[b]] += lm[a, b]
                    stiff[ids[a], ids[b]] += lk[a, b]
                    react[ids[a], ids[b]] += lr[a, b]
        right_coeff = diffusion * (1.0 + diffusion_slope) * (1.0 + diffusion_time * time_value)
        right_transfer = float(cfg.get("right_transfer", 0.0))
        stiff[-1, -1] += right_transfer
        load[-1] += right_coeff * (float(cfg["right_flux"]) + float(cfg.get("right_flux_slope", 0.0)) * time_value)
        load[-1] += right_transfer * (float(cfg.get("right_ambient", 0.0)) + float(cfg.get("right_ambient_slope", 0.0)) * time_value)
        probe_transfer = float(cfg.get("probe_transfer", 0.0))
        if probe_transfer != 0.0:
            xp = min(max(float(cfg.get("probe_x", 0.0)), nodes[0]), nodes[-1])
            elem = int(np.searchsorted(nodes, xp, side="right") - 1)
            elem = min(max(elem, 0), n - 2)
            span = nodes[elem + 1] - nodes[elem]
            right_weight = (xp - nodes[elem]) / span
            phi = np.array([1.0 - right_weight, right_weight])
            ids = np.array([elem, elem + 1])
            ambient = float(cfg.get("probe_ambient", 0.0)) + float(cfg.get("probe_ambient_slope", 0.0)) * time_value
            for a in range(2):
                load[ids[a]] += probe_transfer * ambient * phi[a]
                for b in range(2):
                    stiff[ids[a], ids[b]] += probe_transfer * phi[a] * phi[b]
        if bool(cfg.get("mass_lump", False)):
            mass = np.diag(np.sum(mass, axis=1))
        return mass, stiff + react, load

    dt = float(cfg["dt"])
    dt_growth = float(cfg.get("dt_growth", 0.0))
    theta = float(cfg.get("theta", 1.0))
    left_base = float(cfg["left_bc"])
    left_slope = float(cfg.get("left_bc_slope", 0.0))
    if start_values is None:
        values = (
            float(cfg["initial_offset"])
            + float(cfg["initial_slope"]) * nodes
            + float(cfg["initial_wave"]) * np.sin(np.pi * nodes)
        )
        values[0] = left_base
    else:
        values = np.array(start_values, dtype=float).copy()
    residuals = []
    for step in range(start_step + 1, int(cfg["steps"]) + 1):
        step_dt = dt * (1.0 + dt_growth * (step - 1))
        time_prev = step_time(cfg, step - 1)
        time_next = step_time(cfg, step)
        left = left_base + left_slope * time_next
        mass_prev, spatial_prev, load_prev = assemble_at(time_prev)
        mass_next, spatial_next, load_next = assemble_at(time_next)
        matrix = mass_next + theta * step_dt * spatial_next
        rhs = (mass_prev - (1.0 - theta) * step_dt * spatial_prev) @ values
        rhs += step_dt * ((1.0 - theta) * load_prev + theta * load_next)
        rhs = rhs - matrix[:, 0] * left
        matrix[0, :] = 0.0
        matrix[:, 0] = 0.0
        matrix[0, 0] = 1.0
        rhs[0] = left
        values = np.linalg.solve(matrix, rhs)
        residuals.append(float(np.linalg.norm(matrix @ values - rhs) / max(1.0, np.linalg.norm(rhs))))
    return nodes, values, residuals


def assert_output_contract(data: dict, cfg: dict) -> None:
    assert set(data) == {"time", "steps", "nodes", "values", "residuals", "checksum"}
    assert data["steps"] == cfg["steps"]
    assert math.isclose(data["time"], step_time(cfg, cfg["steps"]), rel_tol=0.0, abs_tol=1e-14)
    assert len(data["nodes"]) == cfg["nodes"]
    assert len(data["values"]) == cfg["nodes"]
    assert len(data["residuals"]) <= cfg["steps"]
    assert all(abs(r) <= 5e-10 for r in data["residuals"])
    text = ",".join(f"{float(v):.17e}" for v in data["values"])
    assert data["checksum"] == hashlib.sha256(text.encode()).hexdigest()


def assert_close_to_reference(data: dict, cfg: dict, atol: float = 2e-9) -> None:
    nodes, expected, _ = reference_solution(cfg)
    np.testing.assert_allclose(np.array(data["nodes"], dtype=float), nodes, rtol=0.0, atol=1e-14)
    np.testing.assert_allclose(np.array(data["values"], dtype=float), expected, rtol=2e-9, atol=atol)


def assert_residuals_match_reference(data: dict, cfg: dict, start_step: int = 0, start_values=None) -> None:
    _, _, expected = reference_solution(cfg, start_step=start_step, start_values=start_values)
    reported = np.array(data["residuals"], dtype=float)
    expected = np.array(expected, dtype=float)
    assert reported.shape == expected.shape
    assert np.all(np.isfinite(reported))
    assert np.max(np.abs(reported)) > 0.0
    np.testing.assert_allclose(reported, expected, rtol=50.0, atol=5e-15)


ZERO_HASH = b"\x00" * 32
SIGNATURE_KEYS = [
    "dt",
    "dt_growth",
    "diffusion",
    "diffusion_slope",
    "diffusion_time",
    "interface_x",
    "interface_contrast",
    "reaction",
    "reaction_slope",
    "reaction_time",
    "left_bc",
    "left_bc_slope",
    "right_flux",
    "right_flux_slope",
    "right_transfer",
    "right_ambient",
    "right_ambient_slope",
    "source",
    "source_slope",
    "source_quadratic",
    "source_wave",
    "source_time",
    "theta",
    "mass_lump",
    "probe_x",
    "probe_transfer",
    "probe_ambient",
    "probe_ambient_slope",
]


def signature_default(key: str) -> float:
    return 1.0 if key in {"theta", "interface_x", "interface_contrast"} else 0.0


def restart_signature_hash(cfg: dict | None) -> bytes:
    if cfg is None:
        return ZERO_HASH
    text = "\n".join(f"{key}={float(cfg.get(key, signature_default(key))):.17e}" for key in SIGNATURE_KEYS)
    return hashlib.sha256(text.encode()).digest()


def record_bytes_and_hash(
    step: int,
    time_value: float,
    values: list[float],
    previous_hash: bytes = ZERO_HASH,
    nodes: list[float] | None = None,
    version: int = 4,
    cfg: dict | None = None,
) -> tuple[bytes, bytes]:
    assert len(previous_hash) == 32
    if nodes is None:
        nodes = np.linspace(0.0, 1.0, len(values)).tolist()
    assert len(nodes) == len(values)
    core = struct.pack("<iiid", version, step, len(values), time_value)
    core += struct.pack(f"<{len(nodes)}d", *nodes)
    core += struct.pack(f"<{len(values)}d", *values)
    core += restart_signature_hash(cfg)
    core += previous_hash
    digest = hashlib.sha256(core).digest()
    payload = core + digest
    return b"JFC1" + struct.pack("<i", len(payload)) + payload, digest


def encode_record(step: int, time_value: float, values: list[float], previous_hash: bytes = ZERO_HASH, nodes=None, version: int = 4, cfg: dict | None = None) -> bytes:
    return record_bytes_and_hash(step, time_value, values, previous_hash, nodes, version, cfg)[0]


def checkpoint_prefix(path: Path) -> tuple[list[int], bytes]:
    data = path.read_bytes()
    steps = []
    offset = 0
    expected_previous_hash = ZERO_HASH
    previous_step = -1
    while offset + 8 <= len(data):
        if data[offset : offset + 4] != b"JFC1":
            break
        size = struct.unpack("<i", data[offset + 4 : offset + 8])[0]
        payload_start = offset + 8
        payload_end = payload_start + size
        if size < 148 or payload_end > len(data):
            break
        version, step, node_count, _ = struct.unpack("<iiid", data[payload_start : payload_start + 20])
        core_end = payload_end - 32
        previous_hash_start = core_end - 32
        if (
            version != 4
            or step <= previous_step
            or node_count < 2
            or size != 116 + 16 * node_count
            or data[previous_hash_start:core_end] != expected_previous_hash
            or hashlib.sha256(data[payload_start:core_end]).digest() != data[core_end:payload_end]
        ):
            break
        steps.append(step)
        previous_step = step
        expected_previous_hash = data[core_end:payload_end]
        offset = payload_end
    return steps, expected_previous_hash


def checkpoint_steps(path: Path) -> list[int]:
    return checkpoint_prefix(path)[0]


def nudge_checkpoint_time(path: Path, target_step: int, delta: float) -> None:
    data = bytearray(path.read_bytes())
    offset = 0
    while offset + 28 <= len(data):
        if data[offset : offset + 4] != b"JFC1":
            break
        size = struct.unpack("<i", data[offset + 4 : offset + 8])[0]
        payload_start = offset + 8
        payload_end = payload_start + size
        if payload_end > len(data) or size < 84:
            break
        version, step, node_count, time_value = struct.unpack("<iiid", data[payload_start : payload_start + 20])
        if version == 4 and node_count >= 2 and size == 116 + 16 * node_count and step == target_step:
            data[payload_start + 12 : payload_start + 20] = struct.pack("<d", time_value + delta)
            core_end = payload_end - 32
            data[core_end:payload_end] = hashlib.sha256(data[payload_start:core_end]).digest()
            path.write_bytes(data)
            return
        offset = payload_end
    raise AssertionError(f"checkpoint record for step {target_step} not found")


def damage_checkpoint_value(path: Path, target_step: int) -> None:
    data = bytearray(path.read_bytes())
    offset = 0
    while offset + 28 <= len(data):
        size = struct.unpack("<i", data[offset + 4 : offset + 8])[0]
        payload_start = offset + 8
        payload_end = payload_start + size
        if data[offset : offset + 4] != b"JFC1" or payload_end > len(data) or size < 92:
            break
        _, step, node_count, _ = struct.unpack("<iiid", data[payload_start : payload_start + 20])
        if size == 116 + 16 * node_count and step == target_step:
            data[payload_start + 20 + 8 * node_count] ^= 0x01
            path.write_bytes(data)
            return
        offset = payload_end
    raise AssertionError(f"checkpoint record for step {target_step} not found")


def test_implicit_fem_solution_matches_independent_reference():
    """Uniform P1 FEM implicit solve matches the independent reference and JSON schema."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg_path = root / "case.toml"
        out = root / "out.json"
        chk = root / "state.bin"
        cfg = write_config(cfg_path, nodes=21, steps=9, dt=0.041, checkpoint_interval=4)
        data = run_driver(cfg_path, out, chk)
        assert_output_contract(data, cfg)
        assert_close_to_reference(data, cfg)
        assert_residuals_match_reference(data, cfg)


def test_restart_output_is_equivalent_to_uninterrupted_run():
    """A continued run keeps the same numerical state as a single full run."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "first.toml"
        restart_cfg = root / "restart.toml"
        full_cfg = root / "full.toml"
        restart_out = root / "restart.json"
        full_out = root / "full.json"
        chk = root / "restart.bin"
        full_chk = root / "full.bin"
        base = {"nodes": 24, "dt": 0.029, "diffusion": 1.17, "left_bc": -0.8, "right_flux": 0.36, "source": -0.07, "initial_wave": 0.41, "checkpoint_interval": 2}
        write_config(first_cfg, steps=5, restart=False, ordering="natural", **base)
        run_driver(first_cfg, root / "first.json", chk)
        with chk.open("ab") as handle:
            handle.write(b"JFC1\x80\x00")
        cfg_restart = write_config(restart_cfg, steps=12, restart=True, ordering="natural", **base)
        write_config(full_cfg, steps=12, restart=False, ordering="natural", **base)
        resumed = run_driver(restart_cfg, restart_out, chk)
        uninterrupted = run_driver(full_cfg, full_out, full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=2e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert os.path.getsize(chk) > os.path.getsize(full_chk) / 3


def test_reordered_sparse_solve_preserves_the_same_discrete_problem():
    """Changing the sparse ordering leaves the accepted discrete solution unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        natural_cfg = root / "natural.toml"
        rcm_cfg = root / "rcm.toml"
        settings = {"nodes": 29, "steps": 8, "dt": 0.063, "diffusion": 0.93, "left_bc": 2.1, "right_flux": -0.58, "source": 0.24, "initial_slope": -0.19, "initial_wave": 0.33, "checkpoint_interval": 5}
        cfg_natural = write_config(natural_cfg, ordering="natural", **settings)
        cfg_rcm = write_config(rcm_cfg, ordering="rcm", **settings)
        natural = run_driver(natural_cfg, root / "natural.json", root / "natural.bin")
        reordered = run_driver(rcm_cfg, root / "rcm.json", root / "rcm.bin")
        assert_output_contract(reordered, cfg_rcm)
        assert_close_to_reference(natural, cfg_natural)
        assert_close_to_reference(reordered, cfg_rcm)
        np.testing.assert_allclose(reordered["values"], natural["values"], rtol=0.0, atol=2e-10)


def test_stretched_mesh_variable_source_restart_matches_reference():
    """A nonuniform mesh and spatial load still restart to the same accepted state."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "stretched_first.toml"
        restart_cfg = root / "stretched_restart.toml"
        full_cfg = root / "stretched_full.toml"
        chk = root / "stretched.bin"
        full_chk = root / "stretched_full.bin"
        settings = {
            "nodes": 37,
            "steps": 15,
            "dt": 0.017,
            "diffusion": 0.64,
            "left_bc": 0.45,
            "right_flux": -0.31,
            "source": -0.08,
            "source_slope": 0.53,
            "source_wave": -0.19,
            "initial_offset": 0.21,
            "initial_slope": -0.37,
            "initial_wave": 0.16,
            "checkpoint_interval": 4,
            "ordering": "rcm",
            "mesh_power": 1.65,
            "theta": 0.71,
            "dt_growth": 0.014,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 8
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "stretched_first.json", chk)
        with chk.open("ab") as handle:
            handle.write(b"trailing-partial-record")
        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "stretched_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "stretched_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=5e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=5e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=3e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]


def test_variable_diffusion_time_dependent_boundary_restart_matches_reference():
    """Variable diffusion, reaction, and time-dependent boundary data survive restart."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "variable_first.toml"
        restart_cfg = root / "variable_restart.toml"
        full_cfg = root / "variable_full.toml"
        chk = root / "variable.bin"
        full_chk = root / "variable_full.bin"
        settings = {
            "nodes": 43,
            "steps": 14,
            "dt": 0.011,
            "diffusion": 0.76,
            "diffusion_slope": 0.48,
            "reaction": 0.29,
            "reaction_slope": -0.52,
            "left_bc": -0.44,
            "left_bc_slope": 0.73,
            "right_flux": 0.16,
            "right_flux_slope": -0.38,
            "source": -0.09,
            "source_slope": 0.21,
            "source_quadratic": -0.34,
            "source_wave": 0.27,
            "source_time": 0.62,
            "initial_offset": 0.17,
            "initial_slope": -0.31,
            "initial_wave": 0.23,
            "checkpoint_interval": 5,
            "ordering": "rcm",
            "mesh_power": 1.58,
            "theta": 0.63,
            "dt_growth": 0.025,
            "right_transfer": 0.44,
            "right_ambient": -0.28,
            "right_ambient_slope": 0.47,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 5
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "variable_first.json", chk)

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "variable_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "variable_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=8e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=8e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=6e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert len(resumed["residuals"]) == settings["steps"] - first_settings["steps"]


def test_time_dependent_spatial_coefficients_use_theta_step_times():
    """Time-varying diffusion and reaction operators are weighted at both theta times."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "operator_first.toml"
        restart_cfg = root / "operator_restart.toml"
        full_cfg = root / "operator_full.toml"
        chk = root / "operator.bin"
        full_chk = root / "operator_full.bin"
        settings = {
            "nodes": 38,
            "steps": 13,
            "dt": 0.012,
            "diffusion": 0.81,
            "diffusion_slope": -0.31,
            "diffusion_time": 0.74,
            "reaction": 0.34,
            "reaction_slope": 0.46,
            "reaction_time": -0.58,
            "left_bc": 0.37,
            "left_bc_slope": -0.43,
            "right_flux": -0.25,
            "right_flux_slope": 0.20,
            "right_transfer": 0.48,
            "right_ambient": -0.19,
            "right_ambient_slope": 0.41,
            "source": 0.08,
            "source_slope": -0.26,
            "source_quadratic": 0.31,
            "source_wave": -0.23,
            "source_time": 0.57,
            "initial_offset": -0.04,
            "initial_slope": 0.22,
            "initial_wave": 0.18,
            "checkpoint_interval": 4,
            "ordering": "rcm",
            "mesh_power": 1.44,
            "theta": 0.54,
            "dt_growth": 0.023,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 4
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "operator_first.json", chk)

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "operator_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "operator_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=9e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=9e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=8e-10)
        assert_residuals_match_reference(resumed, settings, start_step=first_settings["steps"], start_values=reference_solution(first_settings)[1])


def test_interior_probe_exchange_participates_in_theta_restart():
    """An interior trace exchange contributes to both theta operators across restart."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "probe_first.toml"
        restart_cfg = root / "probe_restart.toml"
        full_cfg = root / "probe_full.toml"
        chk = root / "probe.bin"
        full_chk = root / "probe_full.bin"
        settings = {
            "nodes": 42,
            "steps": 16,
            "dt": 0.0108,
            "diffusion": 0.77,
            "diffusion_slope": 0.29,
            "diffusion_time": 0.44,
            "reaction": 0.18,
            "reaction_slope": -0.37,
            "reaction_time": 0.31,
            "left_bc": -0.29,
            "left_bc_slope": 0.48,
            "right_flux": 0.13,
            "right_flux_slope": -0.16,
            "right_transfer": 0.26,
            "right_ambient": -0.11,
            "right_ambient_slope": 0.22,
            "probe_x": 0.436,
            "probe_transfer": 0.71,
            "probe_ambient": 0.38,
            "probe_ambient_slope": -0.27,
            "source": 0.06,
            "source_slope": -0.25,
            "source_quadratic": 0.19,
            "source_wave": -0.24,
            "source_time": 0.36,
            "initial_offset": -0.08,
            "initial_slope": 0.20,
            "initial_wave": 0.17,
            "checkpoint_interval": 5,
            "ordering": "rcm",
            "mesh_power": 1.47,
            "theta": 0.56,
            "dt_growth": 0.018,
        }
        first_settings = dict(settings, steps=5)
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "probe_first.json", chk)

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "probe_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "probe_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=9e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=9e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=8e-10)
        _, start_values, _ = reference_solution(first_settings)
        assert_residuals_match_reference(resumed, settings, start_step=first_settings["steps"], start_values=start_values)


def test_restart_accepts_roundoff_sized_time_difference_and_reports_current_residuals():
    """Roundoff-sized checkpoint times are accepted but residuals stay invocation-local."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "roundoff_first.toml"
        restart_cfg = root / "roundoff_restart.toml"
        full_cfg = root / "roundoff_full.toml"
        chk = root / "roundoff.bin"
        full_chk = root / "roundoff_full.bin"
        settings = {
            "nodes": 27,
            "steps": 9,
            "dt": 0.031,
            "diffusion": 1.04,
            "diffusion_slope": 0.19,
            "reaction": 0.11,
            "reaction_slope": 0.43,
            "left_bc": 0.28,
            "left_bc_slope": -0.17,
            "right_flux": -0.24,
            "right_flux_slope": 0.22,
            "source": 0.06,
            "source_slope": -0.32,
            "source_time": -0.41,
            "initial_offset": -0.13,
            "initial_slope": 0.26,
            "initial_wave": -0.15,
            "checkpoint_interval": 4,
            "ordering": "natural",
            "mesh_power": 1.18,
            "theta": 0.76,
            "dt_growth": 0.013,
            "right_transfer": 0.27,
            "right_ambient": 0.31,
            "right_ambient_slope": -0.16,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 4
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "roundoff_first.json", chk)
        expected_time = step_time(settings, first_settings["steps"])
        nudge_checkpoint_time(chk, first_settings["steps"], 16.0 * np.finfo(float).eps * max(1.0, abs(expected_time)))

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "roundoff_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "roundoff_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=7e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=7e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=5e-10)
        assert len(resumed["residuals"]) == settings["steps"] - first_settings["steps"]
        _, start_values, _ = reference_solution(first_settings)
        assert_residuals_match_reference(resumed, settings, start_step=first_settings["steps"], start_values=start_values)


def test_restart_uses_the_valid_checkpoint_prefix_after_payload_damage():
    """Restart discards a damaged suffix and reproduces an uninterrupted solve."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "first.toml"
        restart_cfg = root / "restart.toml"
        full_cfg = root / "full.toml"
        chk = root / "damaged.bin"
        full_chk = root / "full.bin"
        settings = {
            "nodes": 32,
            "steps": 12,
            "dt": 0.016,
            "dt_growth": 0.019,
            "theta": 0.59,
            "diffusion": 0.86,
            "diffusion_slope": 0.37,
            "reaction": 0.24,
            "reaction_slope": -0.39,
            "left_bc": -0.51,
            "left_bc_slope": 0.29,
            "right_flux": 0.23,
            "right_flux_slope": -0.18,
            "right_transfer": 0.41,
            "right_ambient": -0.22,
            "right_ambient_slope": 0.35,
            "source": 0.12,
            "source_slope": -0.31,
            "source_quadratic": 0.26,
            "source_wave": -0.17,
            "source_time": 0.38,
            "initial_offset": 0.08,
            "initial_slope": -0.25,
            "initial_wave": 0.21,
            "checkpoint_interval": 3,
            "ordering": "rcm",
            "mesh_power": 1.36,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 6
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "first.json", chk)
        damage_checkpoint_value(chk, 6)

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=8e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=7e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert len(resumed["residuals"]) == settings["steps"] - 3
        assert checkpoint_steps(chk) == [3, 6, 9, 12]


def test_reaction_quadratic_source_restart_discards_future_checkpoint_records():
    """Restart keeps the last compatible state with a variable reaction operator."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "layered_first.toml"
        restart_cfg = root / "layered_restart.toml"
        full_cfg = root / "layered_full.toml"
        chk = root / "layered.bin"
        full_chk = root / "layered_full.bin"
        settings = {
            "nodes": 34,
            "steps": 11,
            "dt": 0.013,
            "diffusion": 1.08,
            "reaction": 0.37,
            "reaction_slope": 0.58,
            "left_bc": -1.05,
            "right_flux": 0.29,
            "source": 0.11,
            "source_slope": -0.46,
            "source_quadratic": 0.72,
            "source_wave": 0.24,
            "initial_offset": -0.18,
            "initial_slope": 0.27,
            "initial_wave": -0.22,
            "checkpoint_interval": 2,
            "ordering": "rcm",
            "mesh_power": 1.42,
            "theta": 0.67,
            "dt_growth": 0.011,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 4
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "layered_first.json", chk)

        # A complete but unusable future record followed by bytes from an interrupted write
        # must not replace the newest compatible restart point.
        with chk.open("ab") as handle:
            handle.write(encode_record(99, step_time(settings, 99), [0.0] * settings["nodes"], cfg=settings))
            handle.write(b"JFC1\x18\x00\x00")

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "layered_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "layered_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=6e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=6e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=4e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [2, 4, 6, 8, 10, 11]


def test_restart_truncates_at_first_future_record_even_if_later_bytes_parse():
    """A semantically incompatible checkpoint record is the restart truncation boundary."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "first.toml"
        restart_cfg = root / "restart.toml"
        full_cfg = root / "full.toml"
        chk = root / "future_then_valid.bin"
        full_chk = root / "full.bin"
        settings = {
            "nodes": 31,
            "steps": 13,
            "dt": 0.019,
            "diffusion": 0.83,
            "reaction": 0.21,
            "reaction_slope": -0.47,
            "left_bc": 0.62,
            "right_flux": -0.17,
            "source": -0.14,
            "source_slope": 0.35,
            "source_quadratic": -0.28,
            "source_wave": 0.31,
            "initial_offset": 0.09,
            "initial_slope": -0.24,
            "initial_wave": 0.18,
            "checkpoint_interval": 3,
            "ordering": "rcm",
            "mesh_power": 1.27,
            "theta": 0.73,
            "dt_growth": 0.017,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 6
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "first.json", chk)

        with chk.open("ab") as handle:
            handle.write(encode_record(99, step_time(settings, 99), [0.0] * settings["nodes"], cfg=settings))
            handle.write(encode_record(9, step_time(settings, 9), [1.0] * settings["nodes"], cfg=settings))
            handle.write(b"JFC1\x10")

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=6e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=6e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=4e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [3, 6, 9, 12, 13]


def test_restart_rejects_broken_checkpoint_hash_chain_before_later_valid_record():
    """A valid-looking record with the wrong previous hash cannot extend the prefix."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "chain_first.toml"
        restart_cfg = root / "chain_restart.toml"
        full_cfg = root / "chain_full.toml"
        chk = root / "chain.bin"
        full_chk = root / "chain_full.bin"
        settings = {
            "nodes": 39,
            "steps": 12,
            "dt": 0.014,
            "diffusion": 0.88,
            "diffusion_slope": 0.41,
            "reaction": 0.19,
            "reaction_slope": -0.33,
            "left_bc": 0.72,
            "left_bc_slope": -0.21,
            "right_flux": -0.26,
            "right_flux_slope": 0.18,
            "right_transfer": 0.36,
            "right_ambient": 0.24,
            "right_ambient_slope": -0.31,
            "source": -0.10,
            "source_slope": 0.28,
            "source_quadratic": -0.22,
            "source_wave": 0.20,
            "source_time": -0.35,
            "initial_offset": -0.07,
            "initial_slope": 0.18,
            "initial_wave": 0.25,
            "checkpoint_interval": 4,
            "ordering": "rcm",
            "mesh_power": 1.49,
            "theta": 0.62,
            "dt_growth": 0.012,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 8
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "chain_first.json", chk)

        bogus, bogus_hash = record_bytes_and_hash(10, step_time(settings, 10), [0.5] * settings["nodes"], ZERO_HASH, cfg=settings)
        later = encode_record(11, step_time(settings, 11), [0.75] * settings["nodes"], bogus_hash, cfg=settings)
        with chk.open("ab") as handle:
            handle.write(bogus)
            handle.write(later)

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "chain_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "chain_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=7e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=7e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=5e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [4, 8, 12]


def test_restart_rejects_wrong_time_record_before_later_compatible_bytes():
    """A complete record with inconsistent time cannot become the restart state."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "first.toml"
        restart_cfg = root / "restart.toml"
        full_cfg = root / "full.toml"
        chk = root / "wrong_time.bin"
        full_chk = root / "full.bin"
        settings = {
            "nodes": 36,
            "steps": 10,
            "dt": 0.023,
            "diffusion": 0.91,
            "reaction": 0.18,
            "reaction_slope": 0.51,
            "left_bc": -0.36,
            "right_flux": 0.27,
            "source": 0.07,
            "source_slope": -0.21,
            "source_quadratic": 0.33,
            "source_wave": -0.29,
            "initial_offset": 0.14,
            "initial_slope": 0.22,
            "initial_wave": -0.17,
            "checkpoint_interval": 4,
            "ordering": "rcm",
            "mesh_power": 1.31,
            "theta": 0.69,
            "dt_growth": 0.015,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 4
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "first.json", chk)

        with chk.open("ab") as handle:
            handle.write(encode_record(8, step_time(settings, 8) + 0.125, [3.0] * settings["nodes"], cfg=settings))
            handle.write(encode_record(6, step_time(settings, 6), [1.0] * settings["nodes"], cfg=settings))

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=6e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=6e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=4e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [4, 8, 10]


def test_restart_truncates_at_non_version_four_record():
    """A correctly hashed record with the wrong version is not a compatible suffix."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "version_first.toml"
        restart_cfg = root / "version_restart.toml"
        full_cfg = root / "version_full.toml"
        chk = root / "version.bin"
        full_chk = root / "version_full.bin"
        settings = {
            "nodes": 30,
            "steps": 10,
            "dt": 0.018,
            "diffusion": 0.82,
            "diffusion_slope": 0.34,
            "reaction": 0.20,
            "reaction_slope": -0.29,
            "left_bc": 0.41,
            "left_bc_slope": -0.13,
            "right_flux": -0.22,
            "right_flux_slope": 0.15,
            "right_transfer": 0.33,
            "right_ambient": -0.18,
            "right_ambient_slope": 0.20,
            "source": 0.09,
            "source_slope": -0.24,
            "source_quadratic": 0.17,
            "source_wave": -0.21,
            "source_time": 0.30,
            "initial_offset": -0.06,
            "initial_slope": 0.16,
            "initial_wave": 0.19,
            "checkpoint_interval": 2,
            "ordering": "rcm",
            "mesh_power": 1.33,
            "theta": 0.66,
            "dt_growth": 0.016,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 4
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "version_first.json", chk)
        assert checkpoint_steps(chk) == [2, 4]
        _, last_hash = checkpoint_prefix(chk)

        bad_values = [2.5 - 0.03 * i for i in range(settings["nodes"])]
        bad_record, bad_hash = record_bytes_and_hash(6, step_time(settings, 6), bad_values, last_hash, version=5, cfg=settings)
        with chk.open("ab") as handle:
            handle.write(bad_record)
            handle.write(encode_record(8, step_time(settings, 8), [1.1] * settings["nodes"], bad_hash, cfg=settings))

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "version_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "version_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=7e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=7e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=5e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [2, 4, 6, 8, 10]


def test_restart_without_remesh_rejects_mismatched_stored_nodes():
    """A node mismatch without restart_remesh makes the first record unusable."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        restart_cfg = root / "node_restart.toml"
        full_cfg = root / "node_full.toml"
        chk = root / "node_mismatch.bin"
        full_chk = root / "node_full.bin"
        settings = {
            "nodes": 28,
            "steps": 9,
            "dt": 0.021,
            "diffusion": 0.97,
            "diffusion_slope": -0.18,
            "reaction": 0.23,
            "reaction_slope": 0.31,
            "left_bc": -0.27,
            "left_bc_slope": 0.22,
            "right_flux": 0.25,
            "right_flux_slope": -0.12,
            "right_transfer": 0.38,
            "right_ambient": 0.14,
            "right_ambient_slope": -0.19,
            "source": -0.04,
            "source_slope": 0.26,
            "source_quadratic": -0.15,
            "source_wave": 0.18,
            "source_time": -0.28,
            "initial_offset": 0.10,
            "initial_slope": -0.17,
            "initial_wave": 0.20,
            "checkpoint_interval": 3,
            "ordering": "rcm",
            "mesh_power": 1.45,
            "theta": 0.70,
            "dt_growth": 0.014,
        }
        stored_nodes = (np.linspace(0.0, 1.0, 17) ** 1.12).tolist()
        stored_values = [math.sin(0.4 + i * 0.09) for i in range(len(stored_nodes))]
        chk.write_bytes(encode_record(3, step_time(settings, 3), stored_values, nodes=stored_nodes, cfg=settings))

        cfg_restart = write_config(restart_cfg, restart=True, restart_remesh=False, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "node_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "node_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=7e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=7e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=5e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [3, 6, 9]


def test_restart_truncates_same_count_coordinate_mismatch_before_later_chain():
    """A same-count but different checkpoint grid still ends the usable prefix."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "grid_first.toml"
        restart_cfg = root / "grid_restart.toml"
        full_cfg = root / "grid_full.toml"
        chk = root / "grid_mismatch.bin"
        full_chk = root / "grid_full.bin"
        settings = {
            "nodes": 33,
            "steps": 11,
            "dt": 0.017,
            "diffusion": 0.89,
            "diffusion_slope": 0.26,
            "reaction": 0.27,
            "reaction_slope": -0.34,
            "left_bc": 0.58,
            "left_bc_slope": -0.24,
            "right_flux": -0.20,
            "right_flux_slope": 0.13,
            "right_transfer": 0.42,
            "right_ambient": -0.16,
            "right_ambient_slope": 0.23,
            "source": 0.03,
            "source_slope": -0.29,
            "source_quadratic": 0.21,
            "source_wave": -0.25,
            "source_time": 0.36,
            "initial_offset": -0.05,
            "initial_slope": 0.14,
            "initial_wave": 0.22,
            "checkpoint_interval": 2,
            "ordering": "rcm",
            "mesh_power": 1.41,
            "theta": 0.61,
            "dt_growth": 0.018,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 4
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "grid_first.json", chk)
        assert checkpoint_steps(chk) == [2, 4]
        current_nodes, _, _ = reference_solution(settings)
        _, last_hash = checkpoint_prefix(chk)

        warped_nodes = current_nodes.copy()
        for i, x in enumerate(warped_nodes[1:-1], start=1):
            warped_nodes[i] = x + 0.0017 * x * (1.0 - x)
        bad_values = [0.8 - 0.015 * i + 0.04 * math.sin(i) for i in range(settings["nodes"])]
        bad_record, bad_hash = record_bytes_and_hash(6, step_time(settings, 6), bad_values, last_hash, nodes=warped_nodes.tolist(), cfg=settings)
        later = encode_record(8, step_time(settings, 8), [0.25] * settings["nodes"], bad_hash, nodes=current_nodes.tolist(), cfg=settings)
        with chk.open("ab") as handle:
            handle.write(bad_record)
            handle.write(later)

        cfg_restart = write_config(restart_cfg, restart=True, restart_remesh=False, **settings)
        write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "grid_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "grid_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=8e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=6e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [2, 4, 6, 8, 10, 11]


def test_restart_rejects_nonfinite_payload_values_before_appending():
    """A valid hash does not make a NaN checkpoint payload compatible."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "finite_first.toml"
        restart_cfg = root / "finite_restart.toml"
        full_cfg = root / "finite_full.toml"
        chk = root / "finite.bin"
        full_chk = root / "finite_full.bin"
        settings = {
            "nodes": 35,
            "steps": 10,
            "dt": 0.015,
            "diffusion": 0.94,
            "diffusion_slope": -0.22,
            "reaction": 0.31,
            "reaction_slope": 0.28,
            "left_bc": -0.48,
            "left_bc_slope": 0.19,
            "right_flux": 0.18,
            "right_flux_slope": -0.11,
            "right_transfer": 0.35,
            "right_ambient": 0.20,
            "right_ambient_slope": -0.27,
            "source": -0.02,
            "source_slope": 0.24,
            "source_quadratic": -0.19,
            "source_wave": 0.17,
            "source_time": -0.32,
            "initial_offset": 0.07,
            "initial_slope": -0.15,
            "initial_wave": 0.26,
            "checkpoint_interval": 3,
            "ordering": "rcm",
            "mesh_power": 1.52,
            "theta": 0.65,
            "dt_growth": 0.016,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 3
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "finite_first.json", chk)
        current_nodes, _, _ = reference_solution(settings)
        _, last_hash = checkpoint_prefix(chk)

        bad_values = [0.11 * math.cos(i) for i in range(settings["nodes"])]
        bad_values[settings["nodes"] // 2] = math.nan
        bad_record, bad_hash = record_bytes_and_hash(6, step_time(settings, 6), bad_values, last_hash, nodes=current_nodes.tolist(), cfg=settings)
        later = encode_record(9, step_time(settings, 9), [0.5] * settings["nodes"], bad_hash, nodes=current_nodes.tolist(), cfg=settings)
        with chk.open("ab") as handle:
            handle.write(bad_record)
            handle.write(later)

        cfg_restart = write_config(restart_cfg, restart=True, restart_remesh=False, **settings)
        write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "finite_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "finite_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=8e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=6e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [3, 6, 9, 10]


def test_restart_rejects_boundary_inconsistent_record_before_later_chain():
    """A hashed checkpoint still must satisfy the left Dirichlet state at its time."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "boundary_first.toml"
        restart_cfg = root / "boundary_restart.toml"
        full_cfg = root / "boundary_full.toml"
        chk = root / "boundary.bin"
        full_chk = root / "boundary_full.bin"
        settings = {
            "nodes": 37,
            "steps": 10,
            "dt": 0.016,
            "diffusion": 0.86,
            "diffusion_slope": 0.28,
            "diffusion_time": 0.39,
            "reaction": 0.22,
            "reaction_slope": -0.36,
            "reaction_time": 0.45,
            "left_bc": -0.34,
            "left_bc_slope": 0.52,
            "right_flux": 0.21,
            "right_flux_slope": -0.15,
            "right_transfer": 0.43,
            "right_ambient": -0.13,
            "right_ambient_slope": 0.34,
            "source": 0.05,
            "source_slope": -0.30,
            "source_quadratic": 0.24,
            "source_wave": 0.20,
            "source_time": -0.37,
            "initial_offset": 0.12,
            "initial_slope": -0.18,
            "initial_wave": 0.27,
            "checkpoint_interval": 3,
            "ordering": "rcm",
            "mesh_power": 1.39,
            "theta": 0.57,
            "dt_growth": 0.019,
        }
        first_settings = dict(settings)
        first_settings["steps"] = 3
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "boundary_first.json", chk)
        current_nodes, _, _ = reference_solution(settings)
        _, last_hash = checkpoint_prefix(chk)

        bad_values = [0.06 * math.sin(i * 0.3) for i in range(settings["nodes"])]
        bad_values[0] = settings["left_bc"] + settings["left_bc_slope"] * step_time(settings, 6) + 0.25
        bad_record, bad_hash = record_bytes_and_hash(6, step_time(settings, 6), bad_values, last_hash, nodes=current_nodes.tolist(), cfg=settings)
        later = encode_record(9, step_time(settings, 9), [0.17] * settings["nodes"], bad_hash, nodes=current_nodes.tolist(), cfg=settings)
        with chk.open("ab") as handle:
            handle.write(bad_record)
            handle.write(later)

        cfg_restart = write_config(restart_cfg, restart=True, restart_remesh=False, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "boundary_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "boundary_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=9e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=9e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=7e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [3, 6, 9, 10]


def test_restart_rejects_problem_hash_mismatch_before_later_chain():
    """A checkpoint from a different evolution contract cannot extend the valid prefix."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "signature_first.toml"
        restart_cfg = root / "signature_restart.toml"
        full_cfg = root / "signature_full.toml"
        chk = root / "signature.bin"
        full_chk = root / "signature_full.bin"
        settings = {
            "nodes": 40,
            "steps": 12,
            "dt": 0.0135,
            "diffusion": 0.84,
            "diffusion_slope": 0.31,
            "diffusion_time": -0.22,
            "reaction": 0.25,
            "reaction_slope": -0.28,
            "reaction_time": 0.34,
            "left_bc": 0.46,
            "left_bc_slope": -0.19,
            "right_flux": -0.18,
            "right_flux_slope": 0.17,
            "right_transfer": 0.37,
            "right_ambient": 0.21,
            "right_ambient_slope": -0.24,
            "probe_x": 0.612,
            "probe_transfer": 0.43,
            "probe_ambient": -0.32,
            "probe_ambient_slope": 0.18,
            "source": -0.03,
            "source_slope": 0.22,
            "source_quadratic": -0.18,
            "source_wave": 0.20,
            "source_time": -0.26,
            "initial_offset": 0.05,
            "initial_slope": -0.16,
            "initial_wave": 0.23,
            "checkpoint_interval": 4,
            "ordering": "rcm",
            "mesh_power": 1.43,
            "theta": 0.61,
            "dt_growth": 0.012,
        }
        first_settings = dict(settings, steps=4)
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "signature_first.json", chk)
        current_nodes, _, _ = reference_solution(settings)
        _, last_hash = checkpoint_prefix(chk)

        other_problem = dict(settings, source=settings["source"] + 0.125)
        bad_values = [0.19 * math.cos(i * 0.4) for i in range(settings["nodes"])]
        bad_record, bad_hash = record_bytes_and_hash(
            8,
            step_time(settings, 8),
            bad_values,
            last_hash,
            nodes=current_nodes.tolist(),
            cfg=other_problem,
        )
        later = encode_record(10, step_time(settings, 10), [0.21] * settings["nodes"], bad_hash, nodes=current_nodes.tolist(), cfg=settings)
        with chk.open("ab") as handle:
            handle.write(bad_record)
            handle.write(later)

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "signature_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "signature_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=9e-9)
        assert_close_to_reference(uninterrupted, cfg_full, atol=9e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=8e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [4, 8, 12]


def test_restart_can_project_a_compatible_checkpoint_onto_a_refined_mesh():
    """An opted-in restart remesh continues from the P1 projection of stored nodes."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "coarse_first.toml"
        restart_cfg = root / "refined_restart.toml"
        chk = root / "remesh.bin"
        settings = {
            "steps": 11,
            "dt": 0.0125,
            "diffusion": 0.79,
            "diffusion_slope": -0.24,
            "reaction": 0.16,
            "reaction_slope": 0.42,
            "left_bc": -0.33,
            "left_bc_slope": 0.26,
            "right_flux": 0.19,
            "right_flux_slope": -0.14,
            "right_transfer": 0.31,
            "right_ambient": -0.17,
            "right_ambient_slope": 0.29,
            "source": 0.05,
            "source_slope": -0.27,
            "source_quadratic": 0.18,
            "source_wave": 0.22,
            "source_time": -0.33,
            "initial_offset": 0.11,
            "initial_slope": -0.20,
            "initial_wave": 0.24,
            "checkpoint_interval": 4,
            "ordering": "rcm",
            "theta": 0.64,
            "dt_growth": 0.018,
        }
        coarse = dict(settings, nodes=19, steps=4, mesh_power=1.72, restart=False)
        refined = dict(settings, nodes=41, mesh_power=1.37, restart=True, restart_remesh=True)
        write_config(first_cfg, **coarse)
        run_driver(first_cfg, root / "coarse.json", chk)

        old_nodes, old_values, _ = reference_solution(coarse)
        new_nodes = np.linspace(0.0, 1.0, refined["nodes"]) ** refined["mesh_power"]
        projected = np.interp(new_nodes, old_nodes, old_values)
        cfg_restart = write_config(restart_cfg, **refined)
        resumed = run_driver(restart_cfg, root / "refined.json", chk)
        _, expected, _ = reference_solution(refined, start_step=coarse["steps"], start_values=projected)
        assert_output_contract(resumed, cfg_restart)
        np.testing.assert_allclose(np.array(resumed["nodes"], dtype=float), new_nodes, rtol=0.0, atol=1e-14)
        np.testing.assert_allclose(np.array(resumed["values"], dtype=float), expected, rtol=2e-9, atol=8e-9)
        assert len(resumed["residuals"]) == refined["steps"] - coarse["steps"]
        assert checkpoint_steps(chk) == [4, 8, 11]


def test_restart_projects_external_checkpoint_and_writes_only_final_record():
    """A valid external checkpoint can be projected before final-only checkpointing."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        restart_cfg = root / "external_restart.toml"
        chk = root / "external.bin"
        common = {
            "steps": 13,
            "dt": 0.0105,
            "diffusion": 0.73,
            "diffusion_slope": 0.33,
            "reaction": 0.26,
            "reaction_slope": -0.37,
            "left_bc": 0.52,
            "left_bc_slope": -0.18,
            "right_flux": -0.21,
            "right_flux_slope": 0.27,
            "right_transfer": 0.39,
            "right_ambient": -0.24,
            "right_ambient_slope": 0.32,
            "source": 0.04,
            "source_slope": -0.23,
            "source_quadratic": 0.16,
            "source_wave": -0.20,
            "source_time": 0.29,
            "initial_offset": -0.09,
            "initial_slope": 0.19,
            "initial_wave": 0.28,
            "checkpoint_interval": 0,
            "ordering": "rcm",
            "theta": 0.58,
            "dt_growth": 0.021,
        }
        stored = dict(common, nodes=18, steps=5, mesh_power=1.82, restart=False)
        refined = dict(common, nodes=44, mesh_power=1.29, restart=True, restart_remesh=True)
        old_nodes, old_values, _ = reference_solution(stored)
        chk.write_bytes(encode_record(stored["steps"], step_time(stored, stored["steps"]), old_values.tolist(), nodes=old_nodes.tolist(), cfg=stored))

        cfg_restart = write_config(restart_cfg, **refined)
        resumed = run_driver(restart_cfg, root / "external_restart.json", chk)
        new_nodes = np.linspace(0.0, 1.0, refined["nodes"]) ** refined["mesh_power"]
        projected = np.interp(new_nodes, old_nodes, old_values)
        _, expected, _ = reference_solution(refined, start_step=stored["steps"], start_values=projected)
        assert_output_contract(resumed, cfg_restart)
        np.testing.assert_allclose(np.array(resumed["nodes"], dtype=float), new_nodes, rtol=0.0, atol=1e-14)
        np.testing.assert_allclose(np.array(resumed["values"], dtype=float), expected, rtol=2e-9, atol=8e-9)
        assert_residuals_match_reference(resumed, refined, start_step=stored["steps"], start_values=projected)
        assert checkpoint_steps(chk) == [stored["steps"], refined["steps"]]


def test_restart_truncates_non_increasing_step_record_before_later_chain():
    """Checkpoint steps must increase even when the hash chain is otherwise valid."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "first.toml"
        restart_cfg = root / "restart.toml"
        full_cfg = root / "full.toml"
        chk = root / "ordered.bin"
        full_chk = root / "full.bin"
        settings = {
            "nodes": 29,
            "steps": 10,
            "dt": 0.019,
            "diffusion": 0.93,
            "diffusion_slope": -0.18,
            "reaction": 0.21,
            "reaction_slope": 0.31,
            "left_bc": 0.28,
            "left_bc_slope": -0.22,
            "right_flux": -0.16,
            "right_flux_slope": 0.11,
            "right_transfer": 0.27,
            "right_ambient": 0.33,
            "right_ambient_slope": -0.09,
            "source": 0.14,
            "source_slope": -0.07,
            "source_quadratic": 0.12,
            "source_wave": -0.19,
            "source_time": 0.23,
            "initial_offset": -0.25,
            "initial_slope": 0.17,
            "initial_wave": 0.29,
            "checkpoint_interval": 3,
            "ordering": "rcm",
            "theta": 0.71,
            "dt_growth": 0.014,
            "mesh_power": 1.41,
        }
        write_config(first_cfg, restart=False, **dict(settings, steps=6))
        run_driver(first_cfg, root / "first.json", chk)
        assert checkpoint_steps(chk) == [3, 6]
        current_nodes = np.linspace(0.0, 1.0, settings["nodes"]) ** settings["mesh_power"]
        _, last_hash = checkpoint_prefix(chk)

        stale_values = [0.37 * math.sin(i + 0.25) for i in range(settings["nodes"])]
        stale_record, stale_hash = record_bytes_and_hash(5, step_time(settings, 5), stale_values, last_hash, nodes=current_nodes.tolist(), cfg=settings)
        later = encode_record(9, step_time(settings, 9), [0.44] * settings["nodes"], stale_hash, nodes=current_nodes.tolist(), cfg=settings)
        with chk.open("ab") as handle:
            handle.write(stale_record)
            handle.write(later)

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=8e-9)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=7e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [3, 6, 9, 10]


def test_restart_from_completed_final_record_does_not_duplicate_checkpoint():
    """A restart at the requested final step leaves the complete checkpoint prefix unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg_path = root / "complete.toml"
        chk = root / "complete.bin"
        settings = {
            "nodes": 25,
            "steps": 7,
            "dt": 0.026,
            "diffusion": 0.68,
            "diffusion_slope": 0.17,
            "reaction": 0.08,
            "left_bc": -0.41,
            "left_bc_slope": 0.19,
            "right_flux": 0.22,
            "right_transfer": 0.24,
            "right_ambient": -0.31,
            "source": 0.09,
            "source_slope": 0.13,
            "source_quadratic": -0.04,
            "source_wave": 0.21,
            "source_time": -0.15,
            "initial_offset": 0.18,
            "initial_slope": -0.23,
            "initial_wave": 0.16,
            "checkpoint_interval": 3,
            "theta": 0.66,
            "dt_growth": 0.009,
            "mesh_power": 1.28,
        }
        write_config(cfg_path, restart=False, **settings)
        first = run_driver(cfg_path, root / "first.json", chk)
        before = chk.read_bytes()
        assert checkpoint_steps(chk) == [3, 6, 7]

        cfg_restart = write_config(cfg_path, restart=True, **settings)
        resumed = run_driver(cfg_path, root / "restart.json", chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=6e-9)
        assert resumed["values"] == first["values"]
        assert resumed["checksum"] == first["checksum"]
        assert resumed["residuals"] == []
        assert chk.read_bytes() == before
        assert checkpoint_steps(chk) == [3, 6, 7]


def test_interface_diffusion_and_lumped_mass_restart_match_reference():
    """A material interface with lumped mass remains restart-equivalent to a full solve."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "interface_first.toml"
        restart_cfg = root / "interface_restart.toml"
        full_cfg = root / "interface_full.toml"
        chk = root / "interface.bin"
        full_chk = root / "interface_full.bin"
        settings = {
            "nodes": 47,
            "steps": 15,
            "dt": 0.0107,
            "dt_growth": 0.017,
            "theta": 0.55,
            "mass_lump": True,
            "diffusion": 0.62,
            "diffusion_slope": 0.36,
            "diffusion_time": -0.21,
            "interface_x": 0.438,
            "interface_contrast": 2.7,
            "reaction": 0.28,
            "reaction_slope": -0.33,
            "reaction_time": 0.26,
            "left_bc": -0.22,
            "left_bc_slope": 0.41,
            "right_flux": 0.18,
            "right_flux_slope": -0.14,
            "right_transfer": 0.45,
            "right_ambient": -0.16,
            "right_ambient_slope": 0.23,
            "probe_x": 0.611,
            "probe_transfer": 0.52,
            "probe_ambient": 0.19,
            "probe_ambient_slope": -0.21,
            "source": 0.07,
            "source_slope": -0.25,
            "source_quadratic": 0.18,
            "source_wave": -0.22,
            "source_time": 0.31,
            "initial_offset": -0.05,
            "initial_slope": 0.17,
            "initial_wave": 0.24,
            "checkpoint_interval": 4,
            "ordering": "rcm",
            "mesh_power": 1.53,
        }
        first_settings = dict(settings, steps=8)
        write_config(first_cfg, restart=False, **first_settings)
        run_driver(first_cfg, root / "interface_first.json", chk)

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        cfg_full = write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "interface_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "interface_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=1.1e-8)
        assert_close_to_reference(uninterrupted, cfg_full, atol=1.1e-8)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=9e-10)
        assert_residuals_match_reference(resumed, settings, start_step=first_settings["steps"], start_values=reference_solution(first_settings)[1])
        assert checkpoint_steps(chk) == [4, 8, 12, 15]


def test_restart_rejects_interface_signature_change_before_valid_suffix():
    """Interface and mass-lumping choices are part of checkpoint compatibility."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first_cfg = root / "sig_first.toml"
        restart_cfg = root / "sig_restart.toml"
        full_cfg = root / "sig_full.toml"
        chk = root / "sig.bin"
        full_chk = root / "sig_full.bin"
        settings = {
            "nodes": 41,
            "steps": 12,
            "dt": 0.0122,
            "dt_growth": 0.016,
            "theta": 0.60,
            "mass_lump": True,
            "diffusion": 0.74,
            "diffusion_slope": -0.27,
            "diffusion_time": 0.24,
            "interface_x": 0.572,
            "interface_contrast": 1.85,
            "reaction": 0.30,
            "reaction_slope": 0.38,
            "reaction_time": -0.19,
            "left_bc": 0.31,
            "left_bc_slope": -0.28,
            "right_flux": -0.17,
            "right_flux_slope": 0.12,
            "right_transfer": 0.33,
            "right_ambient": 0.26,
            "right_ambient_slope": -0.20,
            "source": -0.04,
            "source_slope": 0.21,
            "source_quadratic": -0.16,
            "source_wave": 0.23,
            "source_time": -0.29,
            "initial_offset": 0.08,
            "initial_slope": -0.18,
            "initial_wave": 0.20,
            "checkpoint_interval": 3,
            "ordering": "rcm",
            "mesh_power": 1.46,
        }
        write_config(first_cfg, restart=False, **dict(settings, steps=6))
        run_driver(first_cfg, root / "sig_first.json", chk)
        nodes, _, _ = reference_solution(settings)
        _, last_hash = checkpoint_prefix(chk)

        other_contract = dict(settings, interface_contrast=settings["interface_contrast"] + 0.4)
        bad_values = [0.23 * math.sin(0.2 * i) for i in range(settings["nodes"])]
        bad_values[0] = settings["left_bc"] + settings["left_bc_slope"] * step_time(settings, 9)
        bad_record, bad_hash = record_bytes_and_hash(9, step_time(settings, 9), bad_values, last_hash, nodes=nodes.tolist(), cfg=other_contract)
        later = encode_record(10, step_time(settings, 10), [0.31] * settings["nodes"], bad_hash, nodes=nodes.tolist(), cfg=settings)
        with chk.open("ab") as handle:
            handle.write(bad_record)
            handle.write(later)

        cfg_restart = write_config(restart_cfg, restart=True, **settings)
        write_config(full_cfg, restart=False, **settings)
        resumed = run_driver(restart_cfg, root / "sig_restart.json", chk)
        uninterrupted = run_driver(full_cfg, root / "sig_full.json", full_chk)
        assert_output_contract(resumed, cfg_restart)
        assert_close_to_reference(resumed, cfg_restart, atol=1.0e-8)
        np.testing.assert_allclose(resumed["values"], uninterrupted["values"], rtol=0.0, atol=8e-10)
        assert resumed["checksum"] == uninterrupted["checksum"]
        assert checkpoint_steps(chk) == [3, 6, 9, 12]
