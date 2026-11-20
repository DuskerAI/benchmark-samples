import csv
import math
import shutil
import subprocess
from pathlib import Path

APP = Path("/opt/swe")
BINARY = APP / "bin" / "shallow_water"
TOL = 2e-11


def run_command(args):
    result = subprocess.run(
        args,
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def build_binary():
    run_command(["make", "clean"])
    run_command(["make", "all"])
    (APP / "out").mkdir(exist_ok=True)
    assert BINARY.exists(), "expected /opt/swe/bin/shallow_water to be built"


def run_case(nx, steps, split, seed, prefix, mode="pair", checkpoint=None):
    args = [
        str(BINARY),
        "--nx",
        str(nx),
        "--steps",
        str(steps),
        "--split",
        str(split),
        "--seed",
        str(seed),
        "--prefix",
        prefix,
        "--mode",
        mode,
    ]
    if checkpoint is not None:
        args.extend(["--checkpoint", str(checkpoint)])
    run_command(args)


def parse_output(path):
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    summary = [row for row in rows if row["record"] == "summary"]
    cells = [row for row in rows if row["record"] == "cell"]
    assert len(summary) == 1, f"expected one summary row in {path}"
    assert cells, f"expected cell rows in {path}"
    return summary[0], cells


def assert_close(left, right, label):
    assert math.isclose(left, right, rel_tol=0.0, abs_tol=TOL), (
        f"{label}: {left} != {right}"
    )


def assert_outputs_match(left_prefix, right_prefix, nx, steps):
    continuous, cont_cells = parse_output(Path(f"{left_prefix}_continuous.csv"))
    restarted, restart_cells = parse_output(Path(f"{right_prefix}_restarted.csv"))
    assert continuous.keys() == restarted.keys()
    assert len(cont_cells) == nx
    assert len(restart_cells) == nx
    assert int(continuous["step"]) == steps
    assert int(restarted["step"]) == steps

    for key in ["mass", "momentum", "energy"]:
        assert_close(
            float(continuous[key]), float(restarted[key]), f"summary {key} changed"
        )

    for left, right in zip(cont_cells, restart_cells):
        assert left["index"] == right["index"]
        for key in ["height", "velocity"]:
            assert_close(
                float(left[key]),
                float(right[key]),
                f"cell {left['index']} {key} changed",
            )


def state_diagnostics(h, u, memory, lag, terrain, stress, tracer, swirl):
    return {
        "mass": sum(h),
        "momentum": sum(height * velocity for height, velocity in zip(h, u)),
        "energy": sum(
            0.5 * height * velocity * velocity + 0.5 * height * height
            for height, velocity in zip(h, u)
        ),
        "memory_l2": math.sqrt(sum(value * value for value in memory)),
        "lag_l2": math.sqrt(sum(value * value for value in lag)),
        "terrain_l2": math.sqrt(sum(value * value for value in terrain)),
        "stress_l2": math.sqrt(sum(value * value for value in stress)),
        "tracer_l2": math.sqrt(sum(value * value for value in tracer)),
        "swirl_l2": math.sqrt(sum(value * value for value in swirl)),
    }


def assert_restart_audit(prefix, nx, seed, splits):
    audit_path = Path(f"{prefix}_restart_audit.csv")
    with audit_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows, f"expected audit rows in {audit_path}"
    assert rows[0].keys() == {
        "segment",
        "step",
        "mass",
        "momentum",
        "energy",
        "phase",
        "pulse",
        "spin",
        "drift",
        "memory_l2",
        "lag_l2",
        "terrain_l2",
        "stress_l2",
        "tracer_l2",
        "swirl_l2",
    }
    assert len(rows) == len(splits), f"expected one audit row per split in {audit_path}"
    current_step = 0
    state = None
    for segment, (row, split) in enumerate(zip(rows, splits), start=1):
        assert int(row["segment"]) == segment
        assert int(row["step"]) == split
        state = reference_state(nx, split, seed, start_step=current_step, state=state)
        h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift = (
            state
        )
        diagnostics = state_diagnostics(
            h, u, memory, lag, terrain, stress, tracer, swirl
        )
        for key, expected in diagnostics.items():
            assert_close(float(row[key]), expected, f"audit {key} segment {segment}")
        for key, expected in [
            ("phase", phase),
            ("pulse", pulse),
            ("spin", spin),
            ("drift", drift),
        ]:
            assert_close(float(row[key]), expected, f"audit {key} segment {segment}")
        current_step = split


def initial_state(nx, seed):
    phase = float(seed % 97) / 97.0
    pulse = 0.002 * math.sin(0.005 * float(seed)) + 0.001 * math.cos(phase)
    spin = 0.0013 * math.cos(0.009 * float(seed)) - 0.0007 * math.sin(phase)
    drift = 0.0009 * math.sin(0.004 * float(seed)) + 0.0006 * math.cos(2.0 * phase)
    h = []
    u = []
    memory = []
    lag = []
    terrain = []
    stress = []
    tracer = []
    swirl = []
    for i in range(1, nx + 1):
        x = float(i - 1) / float(nx)
        h.append(
            1.0
            + 0.08 * math.sin(6.283185307179586 * (x + phase))
            + 0.015 * math.cos(18.84955592153876 * x)
        )
        u.append(0.03 * math.cos(6.283185307179586 * (x - 0.5 * phase)))
        memory.append(
            0.006 * math.sin(12.566370614359172 * x + 0.37 * phase)
            - 0.004 * math.cos(25.132741228718345 * x - 0.19 * phase)
        )
        lag.append(
            0.0035 * math.cos(31.41592653589793 * x + 0.13 * phase)
            + 0.0018 * math.sin(43.982297150257104 * x - 0.007 * float(seed))
        )
        terrain.append(
            0.0042 * math.sin(9.42477796076938 * x + 0.23 * phase)
            - 0.0026 * math.cos(15.707963267948966 * x - 0.003 * float(seed))
        )
        stress.append(
            0.0029 * math.sin(21.991148575128552 * x + 0.17 * phase)
            + 0.0011 * math.cos(28.274333882308138 * x - 0.002 * float(seed))
        )
        tracer.append(
            0.0016 * math.sin(34.55751918948772 * x + pulse)
            - 0.0012 * math.cos(18.2212373908208 * x - spin)
        )
        swirl.append(
            0.0019 * math.cos(40.840704496667314 * x + drift)
            + 0.0014 * math.sin(23.561944901923447 * x - phase)
        )
    return h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift


def advance_state(
    nx,
    step,
    seed,
    h,
    u,
    memory,
    lag,
    terrain,
    stress,
    tracer,
    swirl,
    phase,
    pulse,
    spin,
    drift,
):
    phase += 0.00073 * math.sin(0.017 * float(seed + step)) + 0.00031 * math.cos(
        0.011 * float(seed - 2 * step)
    )
    pulse += 0.00019 * math.sin(0.013 * float(seed + 7 * step)) - 0.00011 * math.cos(
        0.019 * float(seed - step)
    )
    spin = (
        0.997 * spin
        + 0.00023 * math.sin(phase + 0.021 * float(step))
        + 0.00014 * math.cos(pulse + 0.017 * float(seed))
    )
    drift = (
        0.991 * drift
        + 0.00003 * sum(swirl) / float(nx)
        + 0.00012 * math.sin(spin + 0.015 * float(step))
    )
    hn = [0.0] * nx
    un = [0.0] * nx
    mn = [0.0] * nx
    ln = [0.0] * nx
    sn = [0.0] * nx
    tn = [0.0] * nx
    wn = [0.0] * nx
    for offset in range(nx):
        i = offset + 1
        im = nx - 1 if i == 1 else offset - 1
        ip = 0 if i == nx else offset + 1
        wave = math.sin(0.071 * float(seed + 3 * step + i))
        source = math.cos(0.043 * float(2 * seed - step + 5 * i))
        cross_cell = memory[offset] - 0.5 * (memory[im] + memory[ip])
        lag_flux = lag[ip] - 0.35 * lag[im] + 0.15 * lag[offset]
        terrain_flux = terrain[ip] - 0.4 * terrain[im] + 0.1 * terrain[offset]
        hn[offset] = (
            h[offset]
            - 0.055 * (u[ip] - u[im])
            + 0.0025 * wave
            + 0.0011 * memory[im]
            - 0.0007 * phase
            + 0.0009 * lag[offset]
            + 0.0006 * terrain_flux
            + 0.00045 * stress[ip]
            + 0.0003 * pulse
            + 0.00085 * tracer[im]
            - 0.00022 * spin
            + 0.00051 * swirl[ip]
            + 0.00027 * drift
        )
        un[offset] = (
            0.992 * u[offset]
            - 0.047 * (h[ip] - h[im])
            + 0.0017 * source
            + 0.0023 * cross_cell
            + 0.0005 * phase
            + 0.0012 * lag_flux
            - 0.0008 * terrain[offset]
            - 0.00055 * stress[im]
            + 0.00025 * pulse
            + 0.00065 * tracer[ip]
            + 0.00019 * spin
            - 0.00042 * swirl[im]
            + 0.00031 * drift
        )
        mn[offset] = (
            0.965 * memory[offset]
            + 0.021 * (h[ip] - h[im])
            + 0.013 * u[offset]
            + 0.002 * phase
            + 0.003 * lag[offset]
            + 0.0015 * terrain_flux
            + 0.0011 * stress[offset]
            + 0.0014 * tracer[offset]
        )
        ln[offset] = (
            0.941 * lag[offset]
            + 0.014 * (u[ip] - u[im])
            + 0.009 * memory[ip]
            - 0.0013 * phase
            + 0.0002 * math.sin(0.029 * float(seed + step * i))
            + 0.0008 * stress[ip]
            - 0.0006 * tracer[im]
        )
        sn[offset] = (
            0.953 * stress[offset]
            + 0.011 * (memory[offset] - lag[im])
            + 0.004 * terrain_flux
            + 0.0003 * pulse
            + 0.00017 * math.sin(0.037 * float(seed + i * step))
            + 0.0007 * tracer[ip]
            - 0.00029 * swirl[im]
        )
        tn[offset] = (
            0.948 * tracer[offset]
            + 0.012 * (stress[ip] - memory[im])
            + 0.006 * lag_flux
            - 0.003 * terrain_flux
            + 0.00041 * spin
            + 0.00033 * swirl[offset]
        )
        wn[offset] = (
            0.936 * swirl[offset]
            + 0.010 * (u[ip] - u[im])
            + 0.006 * (h[ip] - 2.0 * h[offset] + h[im])
            + 0.002 * terrain_flux
            + 0.0017 * tracer[offset]
            + 0.0003 * drift
        )
    return hn, un, mn, ln, sn, tn, wn, phase, pulse, spin, drift


def reference_state(nx, steps, seed, start_step=0, state=None):
    if state is None:
        h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift = (
            initial_state(nx, seed)
        )
    else:
        h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift = (
            state
        )
    for step in range(start_step + 1, steps + 1):
        h, u, memory, lag, stress, tracer, swirl, phase, pulse, spin, drift = (
            advance_state(
                nx,
                step,
                seed,
                h,
                u,
                memory,
                lag,
                terrain,
                stress,
                tracer,
                swirl,
                phase,
                pulse,
                spin,
                drift,
            )
        )
    return h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift


def assert_cells_match_reference(path, nx, steps, seed, start_step=0, state=None):
    summary, cells = parse_output(path)
    expected_h, expected_u, *_ = reference_state(
        nx, steps, seed, start_step=start_step, state=state
    )
    expected_mass = sum(expected_h)
    expected_momentum = sum(
        height * velocity for height, velocity in zip(expected_h, expected_u)
    )
    expected_energy = sum(
        0.5 * height * velocity * velocity + 0.5 * height * height
        for height, velocity in zip(expected_h, expected_u)
    )

    assert_close(float(summary["mass"]), expected_mass, f"mass in {path}")
    assert_close(float(summary["momentum"]), expected_momentum, f"momentum in {path}")
    assert_close(float(summary["energy"]), expected_energy, f"energy in {path}")
    for row, expected_height, expected_velocity in zip(cells, expected_h, expected_u):
        assert_close(
            float(row["height"]),
            expected_height,
            f"height cell {row['index']} in {path}",
        )
        assert_close(
            float(row["velocity"]),
            expected_velocity,
            f"velocity cell {row['index']} in {path}",
        )


def read_checkpoint(path):
    lines = path.read_text().splitlines()
    nx, step, seed, phase, pulse, spin, drift = lines[0].split()
    h = []
    u = []
    memory = []
    lag = []
    terrain = []
    stress = []
    tracer = []
    swirl = []
    for expected, line in enumerate(lines[1:], start=1):
        idx, height, velocity, retained, lagged, surface, cell_stress, eddy, spinup = (
            line.split()
        )
        assert int(idx) == expected
        h.append(float(height))
        u.append(float(velocity))
        memory.append(float(retained))
        lag.append(float(lagged))
        terrain.append(float(surface))
        stress.append(float(cell_stress))
        tracer.append(float(eddy))
        swirl.append(float(spinup))
    return (
        int(nx),
        int(step),
        int(seed),
        float(phase),
        float(pulse),
        float(spin),
        float(drift),
        h,
        u,
        memory,
        lag,
        terrain,
        stress,
        tracer,
        swirl,
        lines,
    )


def test_default_make_run_produces_restart_equivalent_science_outputs():
    """The default scientific run must build and compare continuous and restarted outputs."""
    shutil.rmtree(APP / "out", ignore_errors=True)
    run_command(["make", "run"])
    assert_outputs_match(
        "/opt/swe/out/default", "/opt/swe/out/default", nx=48, steps=72
    )
    assert_cells_match_reference(
        Path("/opt/swe/out/default_continuous.csv"), nx=48, steps=72, seed=19
    )
    assert_restart_audit("/opt/swe/out/default", nx=48, seed=19, splits=[31])
    assert (APP / "out" / "default_restart.chk").exists()


def test_checkpoint_restart_matches_for_split_boundaries_and_generated_cases():
    """Restart equivalence must hold for boundary splits and varied generated cases."""
    build_binary()
    cases = [
        (9, 13, 0, 5, "/opt/swe/out/nested/zero_split"),
        (16, 22, 22, 101, "/opt/swe/out/full_split"),
        (33, 41, 1, 7, "/opt/swe/out/early"),
        (64, 96, 47, 113, "/opt/swe/out/middle"),
        (79, 118, 117, 29, "/opt/swe/out/late"),
        (121, 150, 149, 251, "/opt/swe/out/wide_late"),
    ]
    for nx, steps, split, seed, prefix in cases:
        if "nested" in prefix:
            assert not Path(prefix).parent.exists(), (
                "nested output parent should not preexist"
            )
        run_case(nx, steps, split, seed, prefix)
        assert_outputs_match(prefix, prefix, nx=nx, steps=steps)
        assert_cells_match_reference(
            Path(f"{prefix}_continuous.csv"), nx=nx, steps=steps, seed=seed
        )
        assert_restart_audit(prefix, nx=nx, seed=seed, splits=[split])


def test_separate_process_checkpoint_resume_uses_real_restart_file():
    """Checkpoint and resume modes must work across processes without a same-run output to copy."""
    build_binary()
    nx, steps, split, seed = 72, 109, 58, 307
    checkpoint = APP / "out" / "standalone" / "restart.chk"
    continuous_prefix = "/opt/swe/out/standalone/reference"
    resumed_prefix = "/opt/swe/out/standalone/resumed"
    run_case(
        nx,
        steps,
        split,
        seed,
        continuous_prefix,
        mode="continuous",
        checkpoint=checkpoint,
    )
    run_case(
        nx,
        steps,
        split,
        seed,
        "/opt/swe/out/unused",
        mode="checkpoint",
        checkpoint=checkpoint,
    )
    Path(f"{continuous_prefix}_restarted.csv").unlink(missing_ok=True)
    run_case(
        nx, steps, split, 999, resumed_prefix, mode="resume", checkpoint=checkpoint
    )
    assert_outputs_match(continuous_prefix, resumed_prefix, nx=nx, steps=steps)
    assert_cells_match_reference(
        Path(f"{resumed_prefix}_restarted.csv"), nx=nx, steps=steps, seed=seed
    )


def test_ordered_split_list_forces_multiple_checkpoint_cycles():
    """A split list must write and reload a checkpoint at each requested segment boundary."""
    build_binary()
    nx, steps, seed = 88, 132, 419
    splits = [7, 29, 63, 101]
    prefix = "/opt/swe/out/multisegment/run"
    checkpoint = APP / "out" / "multisegment" / "restart.chk"
    trace = APP / "out" / "multisegment" / "trace.log"
    trace.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "strace",
            "-qq",
            "-e",
            "trace=openat",
            "-o",
            str(trace),
            str(BINARY),
            "--nx",
            str(nx),
            "--steps",
            str(steps),
            "--split",
            ",".join(str(split) for split in splits),
            "--seed",
            str(seed),
            "--prefix",
            prefix,
            "--checkpoint",
            str(checkpoint),
        ]
    )
    file_nx, file_step, file_seed, *_ = read_checkpoint(checkpoint)
    assert (file_nx, file_step, file_seed) == (nx, splits[-1], seed)
    trace_text = trace.read_text()
    assert trace_text.count(str(checkpoint)) >= 2 * len(splits)
    assert trace_text.count("O_RDONLY") >= len(splits)
    assert_outputs_match(prefix, prefix, nx=nx, steps=steps)
    assert_cells_match_reference(
        Path(f"{prefix}_continuous.csv"), nx=nx, steps=steps, seed=seed
    )
    assert_restart_audit(prefix, nx=nx, seed=seed, splits=splits)


def test_repeated_split_boundaries_are_real_restart_cycles():
    """Repeated ordered split entries still require checkpoint write/read cycles."""
    build_binary()
    nx, steps, seed = 68, 102, 601
    splits = [0, 17, 17, 54, 102]
    prefix = "/opt/swe/out/repeated_segments/run"
    checkpoint = APP / "out" / "repeated_segments" / "restart.chk"
    trace = APP / "out" / "repeated_segments" / "trace.log"
    trace.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "strace",
            "-qq",
            "-e",
            "trace=openat",
            "-o",
            str(trace),
            str(BINARY),
            "--nx",
            str(nx),
            "--steps",
            str(steps),
            "--split",
            "0, 17,17, 54,102",
            "--seed",
            str(seed),
            "--prefix",
            prefix,
            "--checkpoint",
            str(checkpoint),
        ]
    )
    file_nx, file_step, file_seed, *_ = read_checkpoint(checkpoint)
    assert (file_nx, file_step, file_seed) == (nx, splits[-1], seed)
    trace_text = trace.read_text()
    assert trace_text.count(str(checkpoint)) >= 2 * len(splits)
    assert trace_text.count("O_RDONLY") >= len(splits)
    assert_outputs_match(prefix, prefix, nx=nx, steps=steps)
    assert_cells_match_reference(
        Path(f"{prefix}_continuous.csv"), nx=nx, steps=steps, seed=seed
    )
    assert_restart_audit(prefix, nx=nx, seed=seed, splits=splits)


def test_resume_treats_checkpoint_contents_as_authoritative():
    """Resume mode must advance from the supplied file contents rather than from CLI defaults."""
    build_binary()
    nx, steps, split, seed = 41, 67, 24, 83
    replacement_seed = 197
    checkpoint = APP / "out" / "authoritative" / "restart.chk"
    prefix = "/opt/swe/out/authoritative/resumed"
    run_case(
        nx,
        steps,
        split,
        seed,
        "/opt/swe/out/authoritative/source",
        mode="checkpoint",
        checkpoint=checkpoint,
    )
    (
        file_nx,
        file_step,
        _,
        phase,
        pulse,
        spin,
        drift,
        h,
        u,
        memory,
        lag,
        terrain,
        stress,
        tracer,
        swirl,
        lines,
    ) = read_checkpoint(checkpoint)
    assert file_nx == nx
    replacement_pulse = pulse + 0.0037
    lines[0] = (
        f"{file_nx} {file_step} {replacement_seed} {phase:.17E} {replacement_pulse:.17E} {spin:.17E} {drift:.17E}"
    )
    checkpoint.write_text("\n".join(lines) + "\n")
    run_case(nx, steps, split, 1, prefix, mode="resume", checkpoint=checkpoint)
    state = (
        h,
        u,
        memory,
        lag,
        terrain,
        stress,
        tracer,
        swirl,
        phase,
        replacement_pulse,
        spin,
        drift,
    )
    assert_cells_match_reference(
        Path(f"{prefix}_restarted.csv"),
        nx=nx,
        steps=steps,
        seed=replacement_seed,
        start_step=file_step,
        state=state,
    )


def test_resume_uses_mutated_primary_and_retained_restart_state():
    """Primary fields and retained memory from the checkpoint must drive resumed physics."""
    build_binary()
    nx, steps, split, seed = 46, 86, 33, 277
    checkpoint = APP / "out" / "primary_mutation" / "restart.chk"
    prefix = "/opt/swe/out/primary_mutation/resumed"
    run_case(
        nx,
        steps,
        split,
        seed,
        "/opt/swe/out/primary_mutation/source",
        mode="checkpoint",
        checkpoint=checkpoint,
    )
    (
        file_nx,
        file_step,
        file_seed,
        phase,
        pulse,
        spin,
        drift,
        _h,
        _u,
        _memory,
        lag,
        terrain,
        stress,
        tracer,
        swirl,
        lines,
    ) = read_checkpoint(checkpoint)
    assert (file_nx, file_step, file_seed) == (nx, split, seed)

    mutated_h = []
    mutated_u = []
    mutated_memory = []
    rewritten = [lines[0]]
    for row_number, line in enumerate(lines[1:], start=1):
        idx, height, velocity, retained, lagged, surface, cell_stress, eddy, spinup = (
            line.split()
        )
        raised = (
            float(height)
            + 0.004 * math.sin(0.21 * row_number)
            - 0.002 * math.cos(0.33 * row_number)
        )
        nudged = (
            float(velocity)
            - 0.003 * math.cos(0.17 * row_number)
            + 0.0015 * math.sin(0.29 * row_number)
        )
        remembered = (
            float(retained)
            + 0.006 * math.sin(0.13 * row_number)
            + 0.002 * math.cos(0.27 * row_number)
        )
        mutated_h.append(raised)
        mutated_u.append(nudged)
        mutated_memory.append(remembered)
        rewritten.append(
            f"{idx} {raised:.17E} {nudged:.17E} {remembered:.17E} {lagged} {surface} {cell_stress} {eddy} {spinup}"
        )
    checkpoint.write_text("\n".join(rewritten) + "\n")

    run_case(nx, steps, split, 17, prefix, mode="resume", checkpoint=checkpoint)
    assert_cells_match_reference(
        Path(f"{prefix}_restarted.csv"),
        nx=nx,
        steps=steps,
        seed=seed,
        start_step=file_step,
        state=(
            mutated_h,
            mutated_u,
            mutated_memory,
            lag,
            terrain,
            stress,
            tracer,
            swirl,
            phase,
            pulse,
            spin,
            drift,
        ),
    )


def test_resume_uses_auxiliary_restart_state_without_reconstruction():
    """Every numeric restart column must be authoritative, including hidden coupled state."""
    build_binary()
    nx, steps, split, seed = 57, 91, 37, 163
    checkpoint = APP / "out" / "auxiliary" / "restart.chk"
    prefix = "/opt/swe/out/auxiliary/resumed"
    run_case(
        nx,
        steps,
        split,
        seed,
        "/opt/swe/out/auxiliary/source",
        mode="checkpoint",
        checkpoint=checkpoint,
    )
    (
        file_nx,
        file_step,
        file_seed,
        phase,
        pulse,
        spin,
        drift,
        h,
        u,
        memory,
        _lag,
        _terrain,
        _stress,
        tracer,
        swirl,
        lines,
    ) = read_checkpoint(checkpoint)
    assert (file_nx, file_step, file_seed) == (nx, split, seed)

    mutated_lag = []
    mutated_terrain = []
    mutated_stress = []
    rewritten = [lines[0]]
    for row_number, line in enumerate(lines[1:], start=1):
        idx, height, velocity, retained, lagged, surface, cell_stress, eddy, spinup = (
            line.split()
        )
        adjusted = (
            float(lagged)
            + 0.0125 * math.sin(0.37 * row_number)
            - 0.006 * math.cos(0.11 * row_number)
        )
        roughened = (
            float(surface)
            - 0.009 * math.cos(0.19 * row_number)
            + 0.004 * math.sin(0.41 * row_number)
        )
        loaded = (
            float(cell_stress)
            + 0.007 * math.sin(0.23 * row_number)
            + 0.003 * math.cos(0.31 * row_number)
        )
        mutated_lag.append(adjusted)
        mutated_terrain.append(roughened)
        mutated_stress.append(loaded)
        rewritten.append(
            f"{idx} {height} {velocity} {retained} {adjusted:.17E} {roughened:.17E} {loaded:.17E} {eddy} {spinup}"
        )
    checkpoint.write_text("\n".join(rewritten) + "\n")

    run_case(nx, steps, split, 5, prefix, mode="resume", checkpoint=checkpoint)
    assert_cells_match_reference(
        Path(f"{prefix}_restarted.csv"),
        nx=nx,
        steps=steps,
        seed=seed,
        start_step=file_step,
        state=(
            h,
            u,
            memory,
            mutated_lag,
            mutated_terrain,
            mutated_stress,
            tracer,
            swirl,
            phase,
            pulse,
            spin,
            drift,
        ),
    )


def test_resume_uses_tracer_and_spin_restart_state():
    """The coupled eddy column and global spin value must survive separate-process resume."""
    build_binary()
    nx, steps, split, seed = 63, 104, 42, 347
    checkpoint = APP / "out" / "eddy_spin" / "restart.chk"
    prefix = "/opt/swe/out/eddy_spin/resumed"
    run_case(
        nx,
        steps,
        split,
        seed,
        "/opt/swe/out/eddy_spin/source",
        mode="checkpoint",
        checkpoint=checkpoint,
    )
    (
        file_nx,
        file_step,
        file_seed,
        phase,
        pulse,
        spin,
        drift,
        h,
        u,
        memory,
        lag,
        terrain,
        stress,
        _tracer,
        swirl,
        lines,
    ) = read_checkpoint(checkpoint)
    assert (file_nx, file_step, file_seed) == (nx, split, seed)

    replacement_spin = spin - 0.0028
    mutated_tracer = []
    rewritten = [
        f"{file_nx} {file_step} {file_seed} {phase:.17E} {pulse:.17E} {replacement_spin:.17E} {drift:.17E}"
    ]
    for row_number, line in enumerate(lines[1:], start=1):
        idx, height, velocity, retained, lagged, surface, cell_stress, eddy, spinup = (
            line.split()
        )
        swirled = (
            float(eddy)
            + 0.0105 * math.cos(0.31 * row_number)
            - 0.0045 * math.sin(0.43 * row_number)
        )
        mutated_tracer.append(swirled)
        rewritten.append(
            f"{idx} {height} {velocity} {retained} {lagged} {surface} {cell_stress} {swirled:.17E} {spinup}"
        )
    checkpoint.write_text("\n".join(rewritten) + "\n")

    run_case(nx, steps, split, 11, prefix, mode="resume", checkpoint=checkpoint)
    assert_cells_match_reference(
        Path(f"{prefix}_restarted.csv"),
        nx=nx,
        steps=steps,
        seed=seed,
        start_step=file_step,
        state=(
            h,
            u,
            memory,
            lag,
            terrain,
            stress,
            mutated_tracer,
            swirl,
            phase,
            pulse,
            replacement_spin,
            drift,
        ),
    )


def test_resume_uses_swirl_and_drift_restart_state():
    """The extra coupled restart layer must be authoritative after a separate resume."""
    build_binary()
    nx, steps, split, seed = 74, 121, 69, 563
    checkpoint = APP / "out" / "swirl_drift" / "restart.chk"
    prefix = "/opt/swe/out/swirl_drift/resumed"
    run_case(
        nx,
        steps,
        split,
        seed,
        "/opt/swe/out/swirl_drift/source",
        mode="checkpoint",
        checkpoint=checkpoint,
    )
    (
        file_nx,
        file_step,
        file_seed,
        phase,
        pulse,
        spin,
        drift,
        h,
        u,
        memory,
        lag,
        terrain,
        stress,
        tracer,
        _swirl,
        lines,
    ) = read_checkpoint(checkpoint)
    assert (file_nx, file_step, file_seed) == (nx, split, seed)

    replacement_drift = drift + 0.0019
    mutated_swirl = []
    rewritten = [
        f"{file_nx} {file_step} {file_seed} {phase:.17E} {pulse:.17E} {spin:.17E} {replacement_drift:.17E}"
    ]
    for row_number, line in enumerate(lines[1:], start=1):
        idx, height, velocity, retained, lagged, surface, cell_stress, eddy, spinup = (
            line.split()
        )
        curled = (
            float(spinup)
            - 0.008 * math.sin(0.27 * row_number)
            + 0.006 * math.cos(0.39 * row_number)
        )
        mutated_swirl.append(curled)
        rewritten.append(
            f"{idx} {height} {velocity} {retained} {lagged} {surface} {cell_stress} {eddy} {curled:.17E}"
        )
    checkpoint.write_text("\n".join(rewritten) + "\n")

    run_case(nx, steps, split, 23, prefix, mode="resume", checkpoint=checkpoint)
    assert_cells_match_reference(
        Path(f"{prefix}_restarted.csv"),
        nx=nx,
        steps=steps,
        seed=seed,
        start_step=file_step,
        state=(
            h,
            u,
            memory,
            lag,
            terrain,
            stress,
            tracer,
            mutated_swirl,
            phase,
            pulse,
            spin,
            replacement_drift,
        ),
    )


def test_resume_rejects_mismatched_grid_and_disordered_checkpoint_rows():
    """Restart files with a contradictory grid or cell order must be rejected."""
    build_binary()
    nx, steps, split, seed = 54, 89, 32, 733
    checkpoint = APP / "out" / "invalid_restart" / "restart.chk"
    run_case(
        nx,
        steps,
        split,
        seed,
        "/opt/swe/out/invalid_restart/source",
        mode="checkpoint",
        checkpoint=checkpoint,
    )
    original = checkpoint.read_text(encoding="utf-8").splitlines()

    bad_grid = checkpoint.with_name("bad_grid.chk")
    fields = original[0].split()
    fields[0] = str(nx + 1)
    bad_grid.write_text(
        " ".join(fields) + "\n" + "\n".join(original[1:]) + "\n", encoding="utf-8"
    )
    bad_grid_prefix = "/opt/swe/out/invalid_restart/bad_grid"
    result = subprocess.run(
        [
            str(BINARY),
            "--nx",
            str(nx),
            "--steps",
            str(steps),
            "--split",
            str(split),
            "--prefix",
            bad_grid_prefix,
            "--mode",
            "resume",
            "--checkpoint",
            str(bad_grid),
        ],
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert result.returncode != 0, (
        "resume should reject a checkpoint with the wrong grid size"
    )
    assert not Path(f"{bad_grid_prefix}_restarted.csv").exists()

    bad_order = checkpoint.with_name("bad_order.chk")
    disordered = original.copy()
    first_data = disordered[1].split()
    first_data[0] = "2"
    disordered[1] = " ".join(first_data)
    bad_order.write_text("\n".join(disordered) + "\n", encoding="utf-8")
    bad_order_prefix = "/opt/swe/out/invalid_restart/bad_order"
    result = subprocess.run(
        [
            str(BINARY),
            "--nx",
            str(nx),
            "--steps",
            str(steps),
            "--split",
            str(split),
            "--prefix",
            bad_order_prefix,
            "--mode",
            "resume",
            "--checkpoint",
            str(bad_order),
        ],
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert result.returncode != 0, (
        "resume should reject a checkpoint with disordered cells"
    )
    assert not Path(f"{bad_order_prefix}_restarted.csv").exists()


def test_resume_rejects_contradictory_step_and_row_count():
    """Restart files must not resume from a future step or the wrong number of cell rows."""
    build_binary()
    nx, steps, split, seed = 49, 77, 28, 881
    checkpoint = APP / "out" / "invalid_restart_shape" / "restart.chk"
    run_case(
        nx,
        steps,
        split,
        seed,
        "/opt/swe/out/invalid_restart_shape/source",
        mode="checkpoint",
        checkpoint=checkpoint,
    )
    original = checkpoint.read_text(encoding="utf-8").splitlines()

    future_step = checkpoint.with_name("future_step.chk")
    fields = original[0].split()
    fields[1] = str(steps + 3)
    future_step.write_text(
        " ".join(fields) + "\n" + "\n".join(original[1:]) + "\n", encoding="utf-8"
    )
    future_prefix = "/opt/swe/out/invalid_restart_shape/future"
    result = subprocess.run(
        [
            str(BINARY),
            "--nx",
            str(nx),
            "--steps",
            str(steps),
            "--split",
            str(split),
            "--prefix",
            future_prefix,
            "--mode",
            "resume",
            "--checkpoint",
            str(future_step),
        ],
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert result.returncode != 0, (
        "resume should reject a checkpoint saved beyond the requested final step"
    )
    assert not Path(f"{future_prefix}_restarted.csv").exists()

    extra_row = checkpoint.with_name("extra_row.chk")
    extra_row.write_text("\n".join(original + [original[-1]]) + "\n", encoding="utf-8")
    extra_prefix = "/opt/swe/out/invalid_restart_shape/extra"
    result = subprocess.run(
        [
            str(BINARY),
            "--nx",
            str(nx),
            "--steps",
            str(steps),
            "--split",
            str(split),
            "--prefix",
            extra_prefix,
            "--mode",
            "resume",
            "--checkpoint",
            str(extra_row),
        ],
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert result.returncode != 0, (
        "resume should reject a checkpoint with extra cell rows"
    )
    assert not Path(f"{extra_prefix}_restarted.csv").exists()


def test_unordered_split_list_and_unknown_mode_are_rejected():
    """Only documented modes and nondecreasing split lists are accepted."""
    build_binary()
    unordered_prefix = "/opt/swe/out/invalid_cli/unordered"
    result = subprocess.run(
        [
            str(BINARY),
            "--nx",
            "42",
            "--steps",
            "64",
            "--split",
            "12,9,30",
            "--seed",
            "43",
            "--prefix",
            unordered_prefix,
            "--mode",
            "pair",
        ],
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert result.returncode != 0, (
        "pair mode should reject split lists that move backward"
    )
    assert not Path(f"{unordered_prefix}_restarted.csv").exists()

    unknown_prefix = "/opt/swe/out/invalid_cli/unknown"
    result = subprocess.run(
        [
            str(BINARY),
            "--nx",
            "42",
            "--steps",
            "64",
            "--split",
            "12",
            "--seed",
            "43",
            "--prefix",
            unknown_prefix,
            "--mode",
            "paired",
        ],
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert result.returncode != 0, "unsupported modes should be rejected"
    assert not Path(f"{unknown_prefix}_restarted.csv").exists()


def test_cli_preserves_schema_and_actual_restart_file_use():
    """Custom prefixes must keep the documented CSV schema and open the checkpoint for reading."""
    build_binary()
    prefix = "/opt/swe/out/schema_case"
    trace = APP / "out" / "schema_case.trace"
    run_command(
        [
            "strace",
            "-qq",
            "-e",
            "trace=openat",
            "-o",
            str(trace),
            str(BINARY),
            "--nx",
            "52",
            "--steps",
            "73",
            "--split",
            "36",
            "--seed",
            "211",
            "--prefix",
            prefix,
        ]
    )
    checkpoint = Path(f"{prefix}_restart.chk")
    assert checkpoint.exists(), "segmented path must write a restart checkpoint"
    trace_text = trace.read_text()
    assert str(checkpoint) in trace_text and "O_RDONLY" in trace_text, (
        "restart checkpoint must be opened for reading"
    )
    header = (
        Path(f"{prefix}_continuous.csv").read_text(encoding="utf-8").splitlines()[0]
    )
    assert header == "record,step,index,height,velocity,mass,momentum,energy"
    assert_outputs_match(prefix, prefix, nx=52, steps=73)
    assert_cells_match_reference(
        Path(f"{prefix}_continuous.csv"), nx=52, steps=73, seed=211
    )
    audit_header = (
        Path(f"{prefix}_restart_audit.csv").read_text(encoding="utf-8").splitlines()[0]
    )
    assert (
        audit_header
        == "segment,step,mass,momentum,energy,phase,pulse,spin,drift,memory_l2,lag_l2,terrain_l2,stress_l2,tracer_l2,swirl_l2"
    )
    assert_restart_audit(prefix, nx=52, seed=211, splits=[36])
