import csv
import math
import pathlib
import subprocess
import struct

import pytest


APP = pathlib.Path("/app")
BIN = APP / "continuation"
ROTATION_DOT_MIN = 0.95


@pytest.fixture(scope="session", autouse=True)
def build_program():
    """Compile the submitted D sources before exercising the numerical program."""
    subprocess.run(["make", "clean", "all"], cwd=APP, check=True, timeout=90)


def run_case(tmp_path, name, *, a, c, x0, ds, steps, direction=1, extra=()):
    """Run the continuation binary and return its CSV path and parsed rows."""
    out = tmp_path / f"{name}.csv"
    cmd = [str(BIN), "--output", str(out), "--a", str(a), "--c", str(c),
           "--x0", str(x0), "--ds", str(ds), "--direction", str(direction),
           "--steps", str(steps), *map(str, extra)]
    subprocess.run(cmd, check=True, timeout=30)
    with out.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return out, rows


def expected_next_ds(ds0, used, iterations):
    """Compute the deterministic step size requested after an accepted row."""
    if iterations <= 3:
        return min(ds0 * 1.6, used * 1.25)
    if iterations >= 6:
        return max(ds0 / 32.0, used * 0.6)
    return used


def assert_adaptation(rows, ds0):
    """Check retry halving and accepted-step adaptation against the public contract."""
    first_used = float(rows[1]["ds"])
    first_rejected = int(rows[1]["rejected"])
    assert abs(first_used - ds0 / (2.0 ** first_rejected)) < 1e-12
    for i in range(1, len(rows) - 1):
        current = rows[i]
        nxt = rows[i + 1]
        requested = expected_next_ds(ds0, float(current["ds"]), int(current["iterations"]))
        actual = float(nxt["ds"])
        rejected = int(nxt["rejected"])
        expected = requested / (2.0 ** rejected)
        assert abs(actual - expected) < 2e-12 * max(1.0, abs(expected))


def assert_trajectory(rows, a, c, steps, ds0):
    """Validate branch, tangent, arclength, iteration, and adaptation invariants."""
    assert len(rows) == steps + 1
    assert list(rows[0]) == ["step", "x", "lambda", "tx", "tl", "ds", "iterations", "rejected"]
    parsed = []
    for i, row in enumerate(rows):
        assert int(row["step"]) == i
        vals = tuple(float(row[k]) for k in ("x", "lambda", "tx", "tl", "ds"))
        assert all(math.isfinite(v) for v in vals)
        x, lam, tx, tl, used = vals
        assert abs(x**3 - a*x + lam - c) < 1e-10
        assert abs(math.hypot(tx, tl) - 1.0) < 2e-12
        assert abs((3.0*x*x - a)*tx + tl) < 2e-12
        assert int(row["iterations"]) <= 7
        assert int(row["rejected"]) >= 0
        if i:
            px, plam, ptx, ptl, _ = parsed[-1]
            assert ROTATION_DOT_MIN <= ptx*tx + ptl*tl <= 1.0 + 2e-12
            assert abs((x-px)*ptx + (lam-plam)*ptl - used) < 2e-10
        else:
            assert used == 0.0
        parsed.append(vals)
    assert_adaptation(rows, ds0)
    return parsed


@pytest.mark.parametrize("a,c,x0,ds,steps", [
    (1.0, 0.0, -1.24, 0.17, 30),
    (0.63, -0.19, -1.08, 0.115, 34),
    (1.47, 0.23, -1.42, 0.21, 32),
])
def test_scientific_trajectories(tmp_path, a, c, x0, ds, steps):
    """Trajectories obey the nonlinear branch, arclength, and orientation invariants."""
    _, rows = run_case(tmp_path, "trajectory", a=a, c=c, x0=x0, ds=ds, steps=steps)
    points = assert_trajectory(rows, a, c, steps, ds)
    xs = [p[0] for p in points]
    fold = math.sqrt(a / 3.0)
    assert min(xs) < -fold and max(xs) > fold


def test_reverse_direction_traverses_both_folds(tmp_path):
    """The opposite orientation follows the same branch geometry without sign flips."""
    a, c, x0, ds, steps = 1.21, -0.08, 1.32, 0.16, 35
    _, rows = run_case(tmp_path, "reverse", a=a, c=c, x0=x0, ds=ds,
                       steps=steps, direction=-1)
    points = assert_trajectory(rows, a, c, steps, ds)
    xs = [p[0] for p in points]
    fold = math.sqrt(a / 3.0)
    assert min(xs) < -fold and max(xs) > fold


def test_rejected_trials_are_atomic(tmp_path):
    """Adaptive retries start from the last accepted state and preserve arclength geometry."""
    _, rows = run_case(tmp_path, "adaptive", a=0.82, c=0.07, x0=-1.31, ds=2.0, steps=18)
    assert_trajectory(rows, 0.82, 0.07, 18, 2.0)
    assert any(int(row["rejected"]) > 0 for row in rows[1:])


def test_rotation_limited_steps_remain_replayable(tmp_path):
    """Large requested steps are halved until tangent rotation stays within bounds."""
    a, c, x0, ds, total, cut = 1.56, -0.21, -1.48, 1.15, 27, 11
    full, full_rows = run_case(tmp_path, "rotation_full", a=a, c=c, x0=x0,
                               ds=ds, steps=total)
    assert_trajectory(full_rows, a, c, total, ds)
    assert sum(int(row["rejected"]) for row in full_rows) >= 3
    checkpoint = tmp_path / "rotation_state.bin"
    run_case(tmp_path, "rotation_prefix", a=a, c=c, x0=x0, ds=ds, steps=cut,
             extra=("--checkpoint", checkpoint, "--checkpoint-every", 1))
    resumed = tmp_path / "rotation_resumed.csv"
    subprocess.run([str(BIN), "--output", str(resumed), "--a", str(a), "--c", str(c),
                    "--direction", "1", "--steps", str(total), "--resume", str(checkpoint)],
                   check=True, timeout=30)
    assert resumed.read_bytes() == full.read_bytes()


def test_restart_matches_uninterrupted_run(tmp_path):
    """A resumed checkpoint reproduces every accepted numerical and adaptive field."""
    a, c, x0, ds, total, cut = 1.13, -0.14, -1.37, 0.29, 31, 12
    full, full_rows = run_case(tmp_path, "full", a=a, c=c, x0=x0, ds=ds, steps=total)
    checkpoint = tmp_path / "state.bin"
    run_case(tmp_path, "prefix", a=a, c=c, x0=x0, ds=ds, steps=cut,
             extra=("--checkpoint", checkpoint, "--checkpoint-every", 4))
    resumed = tmp_path / "resumed.csv"
    subprocess.run([str(BIN), "--output", str(resumed), "--a", str(a), "--c", str(c),
                    "--ds", str(ds), "--direction", "1", "--steps", str(total),
                    "--resume", str(checkpoint)], check=True, timeout=30)
    assert resumed.read_bytes() == full.read_bytes()
    assert_trajectory(full_rows, a, c, total, ds)


def test_adaptive_checkpoint_keeps_retry_state(tmp_path):
    """A checkpoint written after rejected trials preserves the next attempted step."""
    a, c, x0, ds, total, cut = 0.82, 0.07, -1.31, 2.0, 18, 9
    full, full_rows = run_case(tmp_path, "adaptive_full", a=a, c=c, x0=x0,
                               ds=ds, steps=total)
    checkpoint = tmp_path / "adaptive_state.bin"
    _, prefix_rows = run_case(tmp_path, "adaptive_prefix", a=a, c=c, x0=x0,
                              ds=ds, steps=cut,
                              extra=("--checkpoint", checkpoint, "--checkpoint-every", 3))
    assert_trajectory(prefix_rows, a, c, cut, ds)
    resumed = tmp_path / "adaptive_resumed.csv"
    subprocess.run([str(BIN), "--output", str(resumed), "--a", str(a), "--c", str(c),
                    "--ds", str(ds), "--direction", "1", "--steps", str(total),
                    "--resume", str(checkpoint)], check=True, timeout=30)
    assert resumed.read_bytes() == full.read_bytes()
    assert_trajectory(full_rows, a, c, total, ds)


def test_checkpoint_is_reproducible(tmp_path):
    """Equivalent runs serialize exactly the same complete checkpoint state."""
    blobs = []
    outputs = []
    for n in (1, 2):
        cp = tmp_path / f"repeat{n}.bin"
        out, rows = run_case(tmp_path, f"repeat{n}", a=0.91, c=0.31, x0=-1.18,
                             ds=0.24, steps=23,
                             extra=("--checkpoint", cp, "--checkpoint-every", 5))
        assert_trajectory(rows, 0.91, 0.31, 23, 0.24)
        blobs.append(cp.read_bytes())
        outputs.append(out.read_bytes())
    assert blobs[0] == blobs[1]
    assert outputs[0] == outputs[1]


def test_resume_uses_checkpointed_initial_and_step_options(tmp_path):
    """Resume gets its initial condition and adaptive base step from the checkpoint."""
    a, c, x0, ds, total, cut = 1.13, -0.14, -1.37, 0.29, 31, 12
    full, full_rows = run_case(tmp_path, "option_full", a=a, c=c, x0=x0, ds=ds, steps=total)
    checkpoint = tmp_path / "option_state.bin"
    run_case(tmp_path, "option_prefix", a=a, c=c, x0=x0, ds=ds, steps=cut,
             extra=("--checkpoint", checkpoint, "--checkpoint-every", 4))
    resumed = tmp_path / "option_resumed.csv"
    subprocess.run([str(BIN), "--output", str(resumed), "--a", str(a), "--c", str(c),
                    "--x0", "0.42", "--ds", "0.051", "--direction", "1",
                    "--steps", str(total), "--resume", str(checkpoint)],
                   check=True, timeout=30)
    assert resumed.read_bytes() == full.read_bytes()
    assert_trajectory(full_rows, a, c, total, ds)


@pytest.mark.parametrize("a,c,x0,ds,steps,direction", [
    (0.74, 0.18, -1.11, 0.097, 42, 1),
    (1.36, -0.27, 1.44, 0.138, 44, -1),
    (1.68, 0.09, -1.53, 0.186, 39, 1),
    (0.58, -0.33, 0.97, 0.084, 43, -1),
])
def test_parameter_sweep_preserves_contracts(tmp_path, a, c, x0, ds, steps, direction):
    """Less round parameters still satisfy the same continuation and retry laws."""
    _, rows = run_case(tmp_path, "sweep", a=a, c=c, x0=x0, ds=ds,
                       steps=steps, direction=direction)
    assert_trajectory(rows, a, c, steps, ds)


def test_two_stage_resume_keeps_checkpoint_chain_replayable(tmp_path):
    """A checkpoint produced by a resumed run can itself resume to the same final CSV."""
    a, c, x0, ds, total = 0.82, 0.07, -1.31, 2.0, 24
    full, full_rows = run_case(tmp_path, "chain_full", a=a, c=c, x0=x0, ds=ds,
                               steps=total, extra=("--checkpoint", tmp_path / "full.bin"))
    first = tmp_path / "chain_first.bin"
    second = tmp_path / "chain_second.bin"
    run_case(tmp_path, "chain_prefix", a=a, c=c, x0=x0, ds=ds, steps=7,
             extra=("--checkpoint", first, "--checkpoint-every", 2))
    mid = tmp_path / "chain_mid.csv"
    subprocess.run([str(BIN), "--output", str(mid), "--a", str(a), "--c", str(c),
                    "--x0", "0.125", "--ds", "0.03125", "--direction", "1",
                    "--steps", "15", "--resume", str(first), "--checkpoint", str(second),
                    "--checkpoint-every", "4"], check=True, timeout=30)
    final = tmp_path / "chain_final.csv"
    subprocess.run([str(BIN), "--output", str(final), "--a", str(a), "--c", str(c),
                    "--direction", "1", "--steps", str(total), "--resume", str(second)],
                   check=True, timeout=30)
    assert final.read_bytes() == full.read_bytes()
    assert_trajectory(full_rows, a, c, total, ds)


def test_corrupted_checkpoint_is_rejected_atomically(tmp_path):
    """A same-size trajectory corruption is rejected before replacing the output CSV."""
    a, c, x0, ds, steps = 1.13, -0.14, -1.37, 0.29, 12
    checkpoint = tmp_path / "corrupt.bin"
    _, rows = run_case(tmp_path, "corrupt_source", a=a, c=c, x0=x0, ds=ds, steps=steps,
                       extra=("--checkpoint", checkpoint, "--checkpoint-every", 3))
    blob = bytearray(checkpoint.read_bytes())
    needle = struct.pack("=d", float(rows[5]["x"]))
    offset = bytes(blob).find(needle)
    assert offset >= 0
    blob[offset + 6] ^= 0x10
    checkpoint.write_bytes(blob)
    out = tmp_path / "should_stay.csv"
    out.write_text("sentinel\n")
    result = subprocess.run([str(BIN), "--output", str(out), "--a", str(a), "--c", str(c),
                             "--direction", "1", "--steps", str(steps),
                             "--resume", str(checkpoint)], timeout=30)
    assert result.returncode != 0
    assert out.read_text() == "sentinel\n"


def test_checkpoint_metadata_corruption_is_rejected_atomically(tmp_path):
    """Selected same-size metadata mutations are rejected before replacing output."""
    a, c, x0, ds, cut, total = 1.13, -0.14, -1.37, 0.29, 12, 20
    checkpoint = tmp_path / "metadata_state.bin"
    run_case(tmp_path, "metadata_source", a=a, c=c, x0=x0, ds=ds, steps=cut,
             extra=("--checkpoint", checkpoint, "--checkpoint-every", 3))
    original = checkpoint.read_bytes()
    assert len(original) > 72
    offsets = range(8, min(len(original) - 8, 80), 8)
    for offset in offsets:
        candidate = tmp_path / f"metadata_corrupt_{offset}.bin"
        blob = bytearray(original)
        blob[offset] ^= 0x21
        candidate.write_bytes(blob)
        out = tmp_path / f"metadata_should_stay_{offset}.csv"
        out.write_text("sentinel\n")
        result = subprocess.run([str(BIN), "--output", str(out), "--a", str(a), "--c", str(c),
                                 "--direction", "1", "--steps", str(total),
                                 "--resume", str(candidate)], timeout=30)
        assert result.returncode != 0, f"corruption at byte {offset} was accepted"
        assert out.read_text() == "sentinel\n"
