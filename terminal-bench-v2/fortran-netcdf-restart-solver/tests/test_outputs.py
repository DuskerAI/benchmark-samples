import csv
import math
import struct
import subprocess
from pathlib import Path

import pytest


APP = Path("/app/task_file")


def build_program():
    subprocess.run(["make", "-C", str(APP), "clean", "all"], check=True, text=True, capture_output=True)


def write_run(path, cfg):
    text = "&run\n" + "\n".join(
        [
            f" nx = {cfg['nx']},",
            f" steps = {cfg['steps']},",
            f" dt = {cfg['dt']:.17g},",
            f" diffusion = {cfg['diffusion']:.17g},",
            f" left_bc = {cfg['left_bc']:.17g},",
            f" right_bc = {cfg['right_bc']:.17g},",
            f" checkpoint_interval = {cfg['checkpoint_interval']},",
            f" restart = {'.true.' if cfg['restart'] else '.false.'}",
        ]
    ) + "\n/\n"
    path.write_text(text)


def run_case(tmp_path, name, cfg, checkpoint):
    cfg_path = tmp_path / f"{name}.nml"
    out_path = tmp_path / f"{name}.csv"
    write_run(cfg_path, cfg)
    subprocess.run([str(APP / "restart_solver"), str(cfg_path), str(out_path), str(checkpoint)], check=True, text=True, capture_output=True, cwd=APP)
    return out_path


def initial_state(cfg):
    n = cfg["nx"]
    values = []
    for i in range(n):
        x = i / (n - 1)
        values.append(cfg["left_bc"] * (1.0 - x) + cfg["right_bc"] * x + math.sin(math.pi * x) + 0.25 * math.sin(3.0 * math.pi * x))
    values[0] = cfg["left_bc"]
    values[-1] = cfg["right_bc"]
    return values


def evolve(cfg, state, start_step=0, target_step=None):
    if target_step is None:
        target_step = cfg["steps"]
    u = list(state)
    n = len(u)
    r = cfg["diffusion"] * cfg["dt"] * (n - 1) * (n - 1)
    u[0] = cfg["left_bc"]
    u[-1] = cfg["right_bc"]
    for _ in range(start_step + 1, target_step + 1):
        nxt = [0.0] * n
        nxt[0] = cfg["left_bc"]
        nxt[-1] = cfg["right_bc"]
        for i in range(1, n - 1):
            nxt[i] = u[i] + r * (u[i - 1] - 2.0 * u[i] + u[i + 1])
        u = nxt
    return u


def read_output(path):
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["index", "x", "u"]
    return [float(row[2]) for row in rows[1:]]


def assert_close(actual, expected, tol=2.5e-12):
    assert len(actual) == len(expected)
    for idx, (a, e) in enumerate(zip(actual, expected), start=1):
        assert abs(a - e) <= tol + tol * abs(e), f"value {idx}: {a} != {e}"


def parse_checkpoint(path, require_clean=True):
    data = path.read_bytes()
    pos = 0
    records = []
    while pos + 8 <= len(data):
        if data[pos : pos + 4] != b"FNC1":
            break
        (nbytes,) = struct.unpack_from("<i", data, pos + 4)
        assert nbytes >= 16
        end = pos + 8 + nbytes
        if end > len(data):
            break
        step, nx, time = struct.unpack_from("<iid", data, pos + 8)
        assert nbytes == 16 + 8 * nx
        values = list(struct.unpack_from("<" + "d" * nx, data, pos + 24))
        records.append({"step": step, "nx": nx, "time": time, "values": values})
        pos = end
    if require_clean:
        assert pos == len(data)
    steps = [record["step"] for record in records]
    assert steps == sorted(set(steps))
    return records, data[pos:]


def write_checkpoint(path, records):
    payloads = []
    for step, values, dt in records:
        payload = struct.pack("<iid", step, len(values), step * dt) + struct.pack("<" + "d" * len(values), *values)
        payloads.append(b"FNC1" + struct.pack("<i", len(payload)) + payload)
    path.write_bytes(b"".join(payloads))


@pytest.fixture(scope="module", autouse=True)
def compiled_program():
    """The submitted program must compile before behavioral checks run."""
    build_program()


def test_restart_output_and_record_stream(tmp_path):
    """A restarted run must match the uninterrupted state and emit parseable records."""
    cfg = {"nx": 43, "steps": 36, "dt": 2.0e-4, "diffusion": 0.7, "left_bc": 0.125, "right_bc": -0.2, "checkpoint_interval": 9, "restart": False}
    full_ckpt = tmp_path / "full.bin"
    full_out = run_case(tmp_path, "full", cfg, full_ckpt)
    expected = evolve(cfg, initial_state(cfg))
    assert_close(read_output(full_out), expected)
    records, _ = parse_checkpoint(full_ckpt)
    assert [r["step"] for r in records] == [9, 18, 27, 36]

    split_ckpt = tmp_path / "split.bin"
    first = dict(cfg, steps=18, checkpoint_interval=6)
    run_case(tmp_path, "first", first, split_ckpt)
    second = dict(cfg, restart=True, steps=36, checkpoint_interval=6)
    split_out = run_case(tmp_path, "second", second, split_ckpt)
    assert_close(read_output(split_out), expected)
    split_records, _ = parse_checkpoint(split_ckpt)
    assert [r["step"] for r in split_records] == [6, 12, 18, 24, 30, 36]


def test_record_choice_and_current_boundaries(tmp_path):
    """A restart must use the newest eligible complete state with the active boundary values."""
    base = {"nx": 31, "steps": 20, "dt": 1.9e-4, "diffusion": 0.72, "left_bc": -0.15, "right_bc": 0.45, "checkpoint_interval": 4, "restart": False}
    checkpoints = []
    for step in (5, 12, 20):
        checkpoints.append((step, evolve(base, initial_state(base), 0, step), base["dt"]))
    ckpt = tmp_path / "existing.bin"
    write_checkpoint(ckpt, checkpoints)

    cfg = dict(base, steps=14, left_bc=0.3, right_bc=-0.7, checkpoint_interval=4, restart=True)
    out = run_case(tmp_path, "resume", cfg, ckpt)
    expected = evolve(cfg, checkpoints[1][1], 12, 14)
    assert_close(read_output(out), expected)
    records, _ = parse_checkpoint(ckpt)
    assert [r["step"] for r in records] == [5, 12, 20]


def test_replay_at_saved_step_with_extra_bytes(tmp_path):
    """A restart at an already saved step must ignore an incomplete suffix and not duplicate it."""
    cfg = {"nx": 37, "steps": 24, "dt": 1.8e-4, "diffusion": 0.75, "left_bc": -0.05, "right_bc": 0.2, "checkpoint_interval": 8, "restart": False}
    ckpt = tmp_path / "journal.bin"
    run_case(tmp_path, "seed", cfg, ckpt)
    before, _ = parse_checkpoint(ckpt)
    ckpt.write_bytes(ckpt.read_bytes() + b"FNC1" + struct.pack("<i", 128) + b"short")

    restart_cfg = dict(cfg, restart=True)
    out = run_case(tmp_path, "again", restart_cfg, ckpt)
    assert_close(read_output(out), evolve(cfg, initial_state(cfg)))
    after, trailing = parse_checkpoint(ckpt, require_clean=False)
    assert [r["step"] for r in after] == [r["step"] for r in before]
    assert trailing.startswith(b"FNC1")
