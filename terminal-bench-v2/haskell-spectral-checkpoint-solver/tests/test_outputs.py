import csv
import math
import subprocess
from pathlib import Path


APP = Path("/app/spectral")


def build() -> None:
    """Compile the Haskell program from the task source tree."""
    subprocess.run(["make", "clean"], cwd=APP, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["make"], cwd=APP, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def write_config(path: Path, **overrides) -> dict:
    """Write a complete key=value config file and return the merged mapping."""
    cfg = {
        "nx": 24,
        "steps": 9,
        "dt": 0.013,
        "diffusivity": 0.006,
        "velocity": 0.41,
        "phase": 0.17,
        "checkpoint_step": 4,
        "restart": "",
    }
    cfg.update(overrides)
    path.write_text("\n".join(f"{k}={v}" for k, v in cfg.items()) + "\n")
    return cfg


def run_case(tmp_path: Path, name: str, **cfg_overrides):
    """Run the spectral executable for one generated config."""
    cfg_path = tmp_path / f"{name}.cfg"
    out_path = tmp_path / f"{name}.csv"
    chk_path = tmp_path / f"{name}.chk"
    cfg = write_config(cfg_path, **cfg_overrides)
    subprocess.run([str(APP / "spectral"), str(cfg_path), str(out_path), str(chk_path)], cwd=APP, check=True)
    return cfg, out_path, chk_path


def run_rejected(cfg_path: Path, out_path: Path, chk_path: Path) -> subprocess.CompletedProcess:
    """Assert that a bad config or checkpoint is rejected without a report."""
    proc = subprocess.run([str(APP / "spectral"), str(cfg_path), str(out_path), str(chk_path)], cwd=APP)
    assert proc.returncode != 0
    assert not out_path.exists()
    return proc


def read_rows(path: Path):
    """Read a report CSV into dictionaries keyed by the report header."""
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def initial(cfg):
    """Compute the stated initial condition on the periodic grid."""
    n = int(cfg["nx"])
    phase = float(cfg["phase"])
    return [
        1.0
        + 0.35 * math.sin(2 * math.pi * (j / n - phase))
        + 0.15 * math.cos(4 * math.pi * (j / n - phase))
        + 0.07 * math.sin(2 * math.pi * (((n // 2 - 1) * j / n) + phase))
        for j in range(n)
    ]


def dft(xs):
    """Compute the unnormalized forward DFT."""
    n = len(xs)
    out = []
    for k in range(n):
        s = 0j
        for j, x in enumerate(xs):
            s += x * complex(math.cos(-2 * math.pi * j * k / n), math.sin(-2 * math.pi * j * k / n))
        out.append(s)
    return out


def idft(hs):
    """Compute the inverse DFT with 1/n normalization."""
    n = len(hs)
    xs = []
    for j in range(n):
        s = 0j
        for k, h in enumerate(hs):
            s += h * complex(math.cos(2 * math.pi * j * k / n), math.sin(2 * math.pi * j * k / n))
        xs.append((s / n).real)
    return xs


def evolve_state(cfg, state, start_step: int, target_step: int):
    """Evolve an arbitrary checkpoint state over the stated time interval."""
    n = int(cfg["nx"])
    t = (target_step - start_step) * float(cfg["dt"])
    diff = float(cfg["diffusivity"])
    vel = float(cfg["velocity"])
    hs = dft(state)
    evolved = []
    for k, h in enumerate(hs):
        kk = k if k <= n // 2 else k - n
        q = 2 * math.pi * kk
        factor = math.exp(-diff * q * q * t) * complex(math.cos(-vel * q * t), math.sin(-vel * q * t))
        evolved.append(h * factor)
    return idft(evolved)


def reference(cfg):
    """Compute the expected final state from the stated initial condition."""
    return evolve_state(cfg, initial(cfg), 0, int(cfg["steps"]))


def assert_report_matches_contract(path: Path, cfg: dict) -> None:
    """Check the report schema, fixed formatting, and repeated statistics."""
    text = path.read_text()
    assert text.endswith("\n")
    lines = text.splitlines()
    assert lines[0] == "step,x,u,mean,l2"
    rows = read_rows(path)
    n = int(cfg["nx"])
    assert len(rows) == n
    vals = [float(r["u"]) for r in rows]
    mu = sum(vals) / n
    l2 = sum(v * v for v in vals) / n
    for j, row in enumerate(rows):
        assert row["step"] == str(cfg["steps"])
        assert row["x"] == f"{j / n:.12f}"
        for field in ("u", "mean", "l2"):
            assert row[field] == f"{float(row[field]):.12f}"
        assert abs(float(row["mean"]) - mu) < 7e-12
        assert abs(float(row["l2"]) - l2) < 7e-12


def checkpoint_hash(body: bytes) -> int:
    """Compute the task's modular byte hash for checkpoint bodies."""
    h = 2166136261
    for b in body:
        h = (h * 16777619 + b) % 1000000007
    return h


def checkpoint_body(cfg: dict, step: int, values) -> bytes:
    """Serialize a checkpoint body without the checksum line."""
    lines = [
        "SPECTRAL1",
        f"step={step}",
        f"nx={int(cfg['nx'])}",
        f"dt={float(cfg['dt']):.17g}",
        f"diffusivity={float(cfg['diffusivity']):.17g}",
        f"velocity={float(cfg['velocity']):.17g}",
        f"phase={float(cfg['phase']):.17g}",
        "values",
        *[f"{v:.17g}" for v in values],
    ]
    return ("\n".join(lines) + "\n").encode()


def write_checkpoint(path: Path, cfg: dict, step: int, values) -> None:
    """Write a valid checksum-protected checkpoint with the supplied state values."""
    body = checkpoint_body(cfg, step, values)
    path.write_bytes(body + f"checksum={checkpoint_hash(body)}\n".encode())


def assert_checkpoint_matches_contract(path: Path, cfg: dict, step: int, expected_values=None) -> None:
    """Check checkpoint schema, hash, metadata, and optional state values."""
    data = path.read_bytes()
    assert data.endswith(b"\n")
    marker = data.rfind(b"checksum=")
    assert marker > 0
    body = data[:marker]
    assert data[marker:] == f"checksum={checkpoint_hash(body)}\n".encode()
    lines = data.decode("utf-8").splitlines()
    assert lines[0] == "SPECTRAL1"
    assert [line.split("=", 1)[0] for line in lines[1:7]] == ["step", "nx", "dt", "diffusivity", "velocity", "phase"]
    assert lines[7] == "values"
    meta = dict(line.split("=", 1) for line in lines[1:7])
    assert int(meta["step"]) == step
    assert int(meta["nx"]) == int(cfg["nx"])
    for key in ("dt", "diffusivity", "velocity", "phase"):
        assert float(meta[key]) == float(cfg[key])
    values = [float(v) for v in lines[8:-1]]
    assert len(values) == int(cfg["nx"])
    assert all(math.isfinite(v) for v in values)
    if expected_values is not None:
        assert max(abs(a - b) for a, b in zip(values, expected_values)) < 5e-10


def test_report_matches_independent_spectral_solution(tmp_path):
    """A normal run follows the exact periodic Fourier heat/advection evolution."""
    build()
    cfg, out, _ = run_case(tmp_path, "direct", nx=32, steps=11, dt=0.009, diffusivity=0.004, velocity=-0.37, phase=0.21, checkpoint_step=11)
    assert_report_matches_contract(out, cfg)
    got = [float(r["u"]) for r in read_rows(out)]
    want = reference(cfg)
    assert max(abs(a - b) for a, b in zip(got, want)) < 5e-10
    assert abs(sum(got) / len(got) - 1.0) < 5e-12
    assert sum(x * x for x in got) / len(got) <= sum(x * x for x in initial(cfg)) / len(got) + 1e-12


def test_restart_report_is_byte_identical_to_uninterrupted_run(tmp_path):
    """A resumed run produces exactly the same final report as its uninterrupted counterpart."""
    build()
    cfg, direct_out, chk = run_case(tmp_path, "segment", nx=48, steps=13, dt=0.007, diffusivity=0.0025, velocity=0.73, phase=0.08, checkpoint_step=5)
    assert_checkpoint_matches_contract(chk, cfg, 5)
    resume_cfg = dict(cfg)
    resume_cfg["restart"] = chk
    resume_cfg["checkpoint_step"] = cfg["steps"]
    _, resumed_out, _ = run_case(tmp_path, "resume", **resume_cfg)
    assert resumed_out.read_bytes() == direct_out.read_bytes()
    assert_report_matches_contract(resumed_out, cfg)


def test_chained_restart_can_emit_a_later_precise_checkpoint(tmp_path):
    """A restart can write a later checkpoint, and a second restart still matches direct bytes."""
    build()
    cfg, direct_out, first_chk = run_case(tmp_path, "chain_direct", nx=64, steps=17, dt=0.0055, diffusivity=0.0017, velocity=-0.61, phase=0.29, checkpoint_step=6)
    assert_checkpoint_matches_contract(first_chk, cfg, 6)
    direct12_cfg = dict(cfg)
    direct12_cfg["checkpoint_step"] = 12
    _, direct12_out, direct12_chk = run_case(tmp_path, "chain_direct12", **direct12_cfg)
    assert direct12_out.read_bytes() == direct_out.read_bytes()
    mid_cfg = dict(cfg)
    mid_cfg["restart"] = first_chk
    mid_cfg["checkpoint_step"] = 12
    _, mid_out, second_chk = run_case(tmp_path, "chain_mid", **mid_cfg)
    assert mid_out.read_bytes() == direct_out.read_bytes()
    assert_checkpoint_matches_contract(second_chk, cfg, 12)
    assert second_chk.read_bytes() == direct12_chk.read_bytes()
    assert second_chk.exists() and second_chk.stat().st_size > 0
    final_cfg = dict(cfg)
    final_cfg["restart"] = second_chk
    final_cfg["checkpoint_step"] = cfg["steps"]
    _, final_out, _ = run_case(tmp_path, "chain_final", **final_cfg)
    assert final_out.read_bytes() == direct_out.read_bytes()
    assert_report_matches_contract(final_out, cfg)


def test_long_multihop_restart_chain_preserves_checkpoint_bytes(tmp_path):
    """Several restart hops preserve absolute-time output and checkpoint bytes."""
    build()
    cfg, direct_out, first_chk = run_case(tmp_path, "long_first", nx=72, steps=31, dt=0.0037, diffusivity=0.0021, velocity=1.19, phase=-0.14, checkpoint_step=4)
    assert_checkpoint_matches_contract(first_chk, cfg, 4)
    direct23_cfg = dict(cfg)
    direct23_cfg["checkpoint_step"] = 23
    _, direct23_out, direct23_chk = run_case(tmp_path, "long_direct23", **direct23_cfg)
    assert direct23_out.read_bytes() == direct_out.read_bytes()

    current_chk = first_chk
    for step in (11, 17, 23):
        hop_cfg = dict(cfg)
        hop_cfg["restart"] = current_chk
        hop_cfg["checkpoint_step"] = step
        _, hop_out, current_chk = run_case(tmp_path, f"long_hop_{step}", **hop_cfg)
        assert hop_out.read_bytes() == direct_out.read_bytes()

    assert current_chk.read_bytes() == direct23_chk.read_bytes()
    assert_checkpoint_matches_contract(current_chk, cfg, 23)
    final_cfg = dict(cfg)
    final_cfg["restart"] = current_chk
    final_cfg["checkpoint_step"] = cfg["steps"]
    _, final_out, _ = run_case(tmp_path, "long_final", **final_cfg)
    assert final_out.read_bytes() == direct_out.read_bytes()
    assert_report_matches_contract(final_out, cfg)


def test_restart_uses_checkpoint_values_as_authoritative_state(tmp_path):
    """Restart evolution uses the stored checkpoint values rather than regenerating them."""
    build()
    cfg, _, source_chk = run_case(tmp_path, "source", nx=36, steps=18, dt=0.0045, diffusivity=0.0013, velocity=-0.86, phase=0.31, checkpoint_step=5)
    assert_checkpoint_matches_contract(source_chk, cfg, 5)
    altered_step = 5
    altered_state = [v + 0.018 * math.cos(2 * math.pi * 5 * j / int(cfg["nx"])) for j, v in enumerate(evolve_state(cfg, initial(cfg), 0, altered_step))]
    altered_chk = tmp_path / "altered.chk"
    write_checkpoint(altered_chk, cfg, altered_step, altered_state)

    resume_cfg = dict(cfg)
    resume_cfg["restart"] = altered_chk
    resume_cfg["checkpoint_step"] = 13
    _, resumed_out, resumed_chk = run_case(tmp_path, "altered_resume", **resume_cfg)
    got = [float(r["u"]) for r in read_rows(resumed_out)]
    want = evolve_state(cfg, altered_state, altered_step, int(cfg["steps"]))
    assert max(abs(a - b) for a, b in zip(got, want)) < 7e-10
    later_state = evolve_state(cfg, altered_state, altered_step, 13)
    assert_checkpoint_matches_contract(resumed_chk, cfg, 13, expected_values=later_state)
    assert_report_matches_contract(resumed_out, cfg)


def test_restart_rejects_each_incompatible_checkpoint_field_without_report(tmp_path):
    """Every checkpoint compatibility field is enforced before any report is written."""
    build()
    cfg, _, chk = run_case(tmp_path, "compat_source", nx=32, steps=15, dt=0.00625, diffusivity=0.003, velocity=0.44, phase=0.12, checkpoint_step=7)
    mismatches = {
        "nx": 34,
        "dt": 0.006125,
        "diffusivity": 0.0034,
        "velocity": 0.45,
        "phase": 0.121,
    }
    for key, value in mismatches.items():
        bad_cfg = dict(cfg)
        bad_cfg["restart"] = chk
        bad_cfg[key] = value
        bad_cfg_path = tmp_path / f"compat_bad_{key}.cfg"
        bad_out = tmp_path / f"compat_bad_{key}.csv"
        bad_chk = tmp_path / f"compat_bad_{key}.chk"
        write_config(bad_cfg_path, **bad_cfg)
        run_rejected(bad_cfg_path, bad_out, bad_chk)

    future_chk = tmp_path / "future.chk"
    write_checkpoint(future_chk, cfg, int(cfg["steps"]) + 1, initial(cfg))
    bad_cfg = dict(cfg)
    bad_cfg["restart"] = future_chk
    bad_cfg["checkpoint_step"] = cfg["steps"]
    bad_cfg_path = tmp_path / "compat_bad_step.cfg"
    write_config(bad_cfg_path, **bad_cfg)
    run_rejected(bad_cfg_path, tmp_path / "compat_bad_step.csv", tmp_path / "compat_bad_step.chk")


def test_malformed_and_byte_corrupted_checkpoints_are_rejected(tmp_path):
    """Malformed or byte-corrupted checkpoint inputs fail without report output."""
    build()
    bad_chk = tmp_path / "garbage.chk"
    bad_chk.write_text("SPECTRAL1\nstep=4\nvalues\nnot-enough-state\n")
    bad_cfg = tmp_path / "malformed.cfg"
    bad_out = tmp_path / "malformed.csv"
    write_config(bad_cfg, restart=bad_chk, checkpoint_step=9)
    run_rejected(bad_cfg, bad_out, tmp_path / "malformed_next.chk")

    cfg, _, good_chk = run_case(tmp_path, "corrupt_source", nx=28, steps=16, dt=0.004, diffusivity=0.0022, velocity=-0.32, phase=0.07, checkpoint_step=5)
    data = bytearray(good_chk.read_bytes())
    for i in range(len(data) - 1, -1, -1):
        if data[i] not in b"\n\r":
            data[i] = ord("0") if data[i] != ord("0") else ord("1")
            break
    corrupted = tmp_path / "corrupted.chk"
    corrupted.write_bytes(bytes(data))
    corrupt_cfg = dict(cfg)
    corrupt_cfg["restart"] = corrupted
    corrupt_cfg["checkpoint_step"] = cfg["steps"]
    corrupt_cfg_path = tmp_path / "corrupted.cfg"
    corrupt_out = tmp_path / "corrupted.csv"
    write_config(corrupt_cfg_path, **corrupt_cfg)
    run_rejected(corrupt_cfg_path, corrupt_out, tmp_path / "corrupted_next.chk")


def test_checkpoint_metadata_order_is_strict_even_with_valid_checksum(tmp_path):
    """Checkpoint metadata lines must use the specified order, independent of checksum validity."""
    build()
    cfg, _, chk = run_case(tmp_path, "ordered_source", nx=30, steps=14, dt=0.00475, diffusivity=0.002, velocity=0.58, phase=-0.22, checkpoint_step=6)
    lines = chk.read_text().splitlines()
    body_lines = lines[:-1]
    body_lines[1], body_lines[2] = body_lines[2], body_lines[1]
    bad_body = ("\n".join(body_lines) + "\n").encode()
    unordered = tmp_path / "unordered.chk"
    unordered.write_bytes(bad_body + f"checksum={checkpoint_hash(bad_body)}\n".encode())
    bad_cfg = dict(cfg)
    bad_cfg["restart"] = unordered
    bad_cfg["checkpoint_step"] = cfg["steps"]
    bad_cfg_path = tmp_path / "unordered.cfg"
    write_config(bad_cfg_path, **bad_cfg)
    run_rejected(bad_cfg_path, tmp_path / "unordered.csv", tmp_path / "unordered_next.chk")


def test_periodic_advection_without_diffusion_is_a_translation(tmp_path):
    """Pure advection preserves the sampled waveform for an integer-period displacement."""
    build()
    cfg, out, _ = run_case(tmp_path, "periodic", nx=40, steps=10, dt=0.0125, diffusivity=0.0, velocity=8.0, phase=0.33, checkpoint_step=0)
    got = [float(r["u"]) for r in read_rows(out)]
    want = initial(cfg)
    assert max(abs(a - b) for a, b in zip(got, want)) < 8e-10
    assert_report_matches_contract(out, cfg)


def test_validation_and_determinism_for_small_grid(tmp_path):
    """Edge-sized valid grids are deterministic and invalid runs fail before producing reports."""
    build()
    cfg, out1, _ = run_case(tmp_path, "edge1", nx=8, steps=0, dt=0.02, diffusivity=0.01, velocity=0.2, phase=-0.125, checkpoint_step=0)
    _, out2, _ = run_case(tmp_path, "edge2", nx=8, steps=0, dt=0.02, diffusivity=0.01, velocity=0.2, phase=-0.125, checkpoint_step=0)
    assert out1.read_bytes() == out2.read_bytes()
    assert not (tmp_path / "edge1.chk").exists()
    assert_report_matches_contract(out1, cfg)
    bad_cfg = tmp_path / "bad.cfg"
    bad_out = tmp_path / "bad.csv"
    bad_chk = tmp_path / "bad.chk"
    write_config(bad_cfg, nx=9, steps=3, checkpoint_step=1)
    run_rejected(bad_cfg, bad_out, bad_chk)

    duplicate_cfg = tmp_path / "duplicate.cfg"
    duplicate_cfg.write_text(
        "nx=8\nsteps=1\ndt=0.02\ndiffusivity=0.01\nvelocity=0.2\n"
        "phase=0.0\ncheckpoint_step=0\nrestart=\nnx=10\n"
    )
    run_rejected(duplicate_cfg, tmp_path / "duplicate.csv", tmp_path / "duplicate.chk")

    unknown_cfg = tmp_path / "unknown.cfg"
    unknown_cfg.write_text(
        "nx=8\nsteps=1\ndt=0.02\ndiffusivity=0.01\nvelocity=0.2\n"
        "phase=0.0\ncheckpoint_step=0\nrestart=\nextra=1\n"
    )
    run_rejected(unknown_cfg, tmp_path / "unknown.csv", tmp_path / "unknown.chk")

    for key, value in (("velocity", "Infinity"), ("phase", "NaN")):
        nonfinite_cfg = tmp_path / f"nonfinite_{key}.cfg"
        nonfinite_cfg.write_text(
            "nx=8\nsteps=1\ndt=0.02\ndiffusivity=0.01\n"
            f"velocity={'0.2' if key != 'velocity' else value}\n"
            f"phase={'0.0' if key != 'phase' else value}\n"
            "checkpoint_step=0\nrestart=\n"
        )
        run_rejected(nonfinite_cfg, tmp_path / f"nonfinite_{key}.csv", tmp_path / f"nonfinite_{key}.chk")
