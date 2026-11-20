import csv
import math
import subprocess
from pathlib import Path

import pytest

ROOT = Path("/app/dg_swe")
BIN = ROOT / "swe_dg"
CSV_HEADER = ["cell", "x", "h_avg", "h_slope", "q_avg", "q_slope"]


@pytest.fixture(scope="session", autouse=True)
def rebuild_binary():
    """The verifier rebuilds the solver once from the submitted source before any case runs."""
    subprocess.run(["make", "clean"], cwd=ROOT, check=True)
    subprocess.run(["make"], cwd=ROOT, check=True)


def base_config(**overrides):
    values = {
        "cells": 48,
        "steps": 8,
        "cfl": 0.18,
        "gravity": 9.81,
        "slope": 0.0,
        "hump": 0.03,
        "dry_floor": 0.001,
        "checkpoint_interval": 4,
        "restart": "false",
    }
    values.update(overrides)
    return values


def write_config(path, **values):
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n")


def run_case(tmp_path, name, **cfg):
    config = tmp_path / f"{name}.cfg"
    output = tmp_path / f"{name}.csv"
    checkpoint = tmp_path / f"{name}.chk"
    write_config(config, **cfg)
    subprocess.run([str(BIN), str(config), str(output), str(checkpoint)], cwd=ROOT, check=True)
    return output, checkpoint


def run_raw(config, output, checkpoint):
    return subprocess.run(
        [str(BIN), str(config), str(output), str(checkpoint)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def load_csv(path):
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == CSV_HEADER
        rows = list(reader)
    assert rows, "the solver wrote no state rows"
    return [
        {
            "cell": int(row["cell"]),
            "x": float(row["x"]),
            "h_avg": float(row["h_avg"]),
            "h_slope": float(row["h_slope"]),
            "q_avg": float(row["q_avg"]),
            "q_slope": float(row["q_slope"]),
        }
        for row in rows
    ]


def assert_same_state(left, right, tol=2e-13):
    assert len(left) == len(right)
    for a, b in zip(left, right):
        assert a["cell"] == b["cell"]
        assert abs(a["x"] - b["x"]) <= tol
        for key in ("h_avg", "h_slope", "q_avg", "q_slope"):
            assert abs(a[key] - b[key]) <= tol, (a["cell"], key, a[key], b[key])


def water_volume(rows):
    return sum(row["h_avg"] for row in rows) / len(rows)


def checkpoint_steps(path):
    return [int(line.split()[3]) for line in path.read_text().splitlines() if line.startswith("BEGIN DGSW1 ")]


def checkpoint_checksum(step, time, rows):
    return step * 0.125 + time + sum(
        h0 * (1 + i) + h1 * (3 + i) + q0 * (5 + i) + q1 * (7 + i)
        for i, (h0, h1, q0, q1) in enumerate(rows, start=1)
    )


def parse_checkpoint_records(path):
    records = []
    lines = path.read_text().splitlines()
    index = 0
    while index < len(lines):
        parts = lines[index].split()
        assert parts[:2] == ["BEGIN", "DGSW1"]
        cells = int(parts[2])
        step = int(parts[3])
        time = float(parts[4])
        begin_checksum = float(parts[5])
        rows = []
        for row_index in range(1, cells + 1):
            row = lines[index + row_index].split()
            assert int(row[0]) == row_index
            rows.append(tuple(float(value) for value in row[1:]))
        end = lines[index + cells + 1].split()
        assert end[0] == "END"
        records.append(
            {
                "cells": cells,
                "step": step,
                "time": time,
                "begin_checksum": begin_checksum,
                "rows": rows,
                "end_checksum": float(end[1]),
            }
        )
        index += cells + 2
    return records


def make_checkpoint_record(cells, step, time, rows, checksum_value):
    body = "\n".join(
        f"{i} {h0:.16E} {h1:.16E} {q0:.16E} {q1:.16E}"
        for i, (h0, h1, q0, q1) in enumerate(rows, start=1)
    )
    return f"BEGIN DGSW1 {cells} {step} {time:.16E} {checksum_value:.16E}\n{body}\nEND {checksum_value:.16E}\n"


def limited_slope(a, b):
    if a * b <= 0.0:
        return 0.0
    return (1.0 if a >= 0.0 else -1.0) * min(abs(a), abs(b), 0.5 * (abs(a) + abs(b)))


def restore_mass(cells, dry_floor, target, h0):
    for _ in range(cells):
        extra = sum(h0) - target
        if abs(extra) <= 1.0e-14 * max(1.0, abs(target)):
            break
        active = sum(value > dry_floor + 1.0e-15 for value in h0)
        if active <= 0:
            break
        share = extra / active
        for i, value in enumerate(h0):
            if value > dry_floor + 1.0e-15:
                h0[i] = max(dry_floor, value - share)


def reference_state(*, cells, steps, cfl, gravity, slope, hump, dry_floor, periodic=True):
    dx = 1.0 / cells
    h0, h1, q0, q1 = [], [], [], []
    for i in range(1, cells + 1):
        x = (i - 0.5) * dx
        g1 = math.exp(-((x - 0.31) / 0.075) ** 2)
        g2 = math.exp(-((x - 0.73) / 0.060) ** 2)
        height = max(dry_floor, dry_floor + 0.018 + hump * g1 - 0.014 * g2)
        h0.append(height)
        left = math.exp(-((x + 0.5 * dx - 0.31) / 0.075) ** 2)
        right = math.exp(-((x - 0.5 * dx - 0.31) / 0.075) ** 2)
        h1.append(0.5 * hump * (left - right))
        q0.append(0.006 * math.sin(6.283185307179586 * x) * max(height - dry_floor, 0.0))
        q1.append(0.25 * q0[-1] * math.cos(6.283185307179586 * x))
    for _ in range(steps):
        fh, fq = [], []
        for face in range(cells + 1):
            if periodic:
                left_idx, right_idx = (cells - 1, 0) if face in (0, cells) else (face - 1, face)
            else:
                left_idx, right_idx = (max(0, face - 1), min(cells - 1, face))
            hl = max(dry_floor, h0[left_idx] + 0.5 * h1[left_idx])
            hr = max(dry_floor, h0[right_idx] - 0.5 * h1[right_idx])
            ql = q0[left_idx] + 0.5 * q1[left_idx]
            qr = q0[right_idx] - 0.5 * q1[right_idx]
            ul = ql / hl
            ur = qr / hr
            flq = ql * ul + 0.5 * gravity * hl * hl
            frq = qr * ur + 0.5 * gravity * hr * hr
            speed = max(abs(ul) + (gravity * hl) ** 0.5, abs(ur) + (gravity * hr) ** 0.5)
            fh.append(0.5 * (ql + qr) - 0.5 * speed * (hr - hl))
            fq.append(0.5 * (flq + frq) - 0.5 * speed * (qr - ql))
        target = sum(h0)
        nh = [h0[i] - cfl * (fh[i + 1] - fh[i]) for i in range(cells)]
        nq = [q0[i] - cfl * (fq[i + 1] - fq[i]) + cfl * slope * h0[i] * dx for i in range(cells)]
        h0 = [max(dry_floor, value) for value in nh]
        q0 = nq
        restore_mass(cells, dry_floor, target, h0)
        h1 = [limited_slope(h0[(i + 1) % cells] - h0[i], h0[i] - h0[(i - 1) % cells]) for i in range(cells)]
        q1 = [limited_slope(q0[(i + 1) % cells] - q0[i], q0[i] - q0[(i - 1) % cells]) for i in range(cells)]
        for i, value in enumerate(h0):
            if value <= 1.05 * dry_floor:
                h0[i] = dry_floor
                q0[i] = 0.0
                q1[i] = 0.0
    return [
        {"cell": i + 1, "x": (i + 0.5) / cells, "h_avg": h0[i], "h_slope": h1[i], "q_avg": q0[i], "q_slope": q1[i]}
        for i in range(cells)
    ]


def test_restart_matches_single_run_after_appended_tail(tmp_path):
    """A resumed run must reproduce the final modal state from the same uninterrupted simulation."""
    common = {"cells": 48, "cfl": 0.17, "gravity": 9.81, "slope": 0.0, "hump": 0.041, "dry_floor": 0.0012}
    full_out, _ = run_case(tmp_path, "full", steps=39, checkpoint_interval=13, restart="false", **common)
    _, checkpoint = run_case(tmp_path, "partial", steps=22, checkpoint_interval=7, restart="false", **common)
    with checkpoint.open("a") as handle:
        handle.write("BEGIN DGSW1 48 999 0.0 0.0\n1 0.0 0.0 0.0 0.0\n")
    cfg = tmp_path / "resume.cfg"
    resumed_out = tmp_path / "resume.csv"
    write_config(cfg, steps=39, checkpoint_interval=5, restart="true", **common)
    subprocess.run([str(BIN), str(cfg), str(resumed_out), str(checkpoint)], cwd=ROOT, check=True)
    assert_same_state(load_csv(full_out), load_csv(resumed_out))


def test_restart_output_bytes_match_uninterrupted_run(tmp_path):
    """A restart must write the same CSV bytes as the equivalent uninterrupted run."""
    common = {"cells": 44, "cfl": 0.163, "gravity": 9.77, "slope": 0.0002, "hump": 0.036, "dry_floor": 0.0016}
    full_out, _ = run_case(tmp_path, "bitwise_full", steps=34, checkpoint_interval=0, restart="false", **common)
    _, checkpoint = run_case(tmp_path, "bitwise_seed", steps=19, checkpoint_interval=19, restart="false", **common)
    cfg = tmp_path / "bitwise_resume.cfg"
    resumed_out = tmp_path / "bitwise_resume.csv"
    write_config(cfg, steps=34, checkpoint_interval=5, restart="true", **common)
    subprocess.run([str(BIN), str(cfg), str(resumed_out), str(checkpoint)], cwd=ROOT, check=True)
    assert resumed_out.read_bytes() == full_out.read_bytes()
    assert load_csv(resumed_out)


def test_checkpoint_interval_zero_preserves_existing_checkpoint_for_fresh_run(tmp_path):
    """A fresh run with checkpointing disabled must leave an existing checkpoint path untouched."""
    config = {
        "cells": 42,
        "steps": 9,
        "cfl": 0.171,
        "gravity": 9.81,
        "slope": 0.0001,
        "hump": 0.034,
        "dry_floor": 0.0013,
        "checkpoint_interval": 0,
        "restart": "false",
    }
    checkpoint = tmp_path / "fresh_no_touch.chk"
    sentinel = b"sentinel checkpoint bytes\nthat must remain byte-identical\n"
    checkpoint.write_bytes(sentinel)
    cfg = tmp_path / "fresh_no_touch.cfg"
    out = tmp_path / "fresh_no_touch.csv"
    write_config(cfg, **config)
    subprocess.run([str(BIN), str(cfg), str(out), str(checkpoint)], cwd=ROOT, check=True)
    assert checkpoint.read_bytes() == sentinel
    actual = load_csv(out)
    expected = reference_state(
        **{key: value for key, value in config.items() if key != "checkpoint_interval" and key != "restart"}
    )
    assert_same_state(actual, expected, tol=2e-12)


def test_checkpoint_interval_zero_preserves_existing_checkpoint_for_restart_run(tmp_path):
    """A restart run with checkpointing disabled must read but not mutate the checkpoint stream."""
    common = {"cells": 46, "cfl": 0.157, "gravity": 9.79, "slope": 0.00027, "hump": 0.033, "dry_floor": 0.00125}
    expected_out, _ = run_case(tmp_path, "zero_restart_full", steps=15, checkpoint_interval=0, restart="false", **common)
    _, checkpoint = run_case(tmp_path, "zero_restart_seed", steps=6, checkpoint_interval=6, restart="false", **common)
    sentinel = checkpoint.read_bytes()
    cfg = tmp_path / "zero_restart_resume.cfg"
    resumed_out = tmp_path / "zero_restart_resume.csv"
    write_config(cfg, steps=15, checkpoint_interval=0, restart="true", **common)
    subprocess.run([str(BIN), str(cfg), str(resumed_out), str(checkpoint)], cwd=ROOT, check=True)
    assert checkpoint.read_bytes() == sentinel
    assert_same_state(load_csv(expected_out), load_csv(resumed_out), tol=2e-12)


def test_conservative_periodic_run_keeps_volume_and_floor(tmp_path):
    """A flat-bed periodic run must conserve water volume while keeping near-dry cells admissible."""
    initial_out, _ = run_case(
        tmp_path,
        "initial",
        cells=72,
        steps=0,
        cfl=0.21,
        gravity=9.81,
        slope=0.0,
        hump=0.019,
        dry_floor=0.0065,
        checkpoint_interval=0,
        restart="false",
    )
    final_out, _ = run_case(
        tmp_path,
        "final",
        cells=72,
        steps=54,
        cfl=0.21,
        gravity=9.81,
        slope=0.0,
        hump=0.019,
        dry_floor=0.0065,
        checkpoint_interval=9,
        restart="false",
    )
    initial = load_csv(initial_out)
    final = load_csv(final_out)
    assert min(row["h_avg"] - 0.5 * abs(row["h_slope"]) for row in final) >= 0.0065 - 2e-14
    assert abs(water_volume(final) - water_volume(initial)) <= 3e-13
    assert abs(final[0]["q_avg"] - final[-1]["q_avg"]) < 3e-3


def test_asymmetric_run_matches_independent_periodic_reference(tmp_path):
    """A long asymmetric run must use periodic fluxes rather than clamped boundary faces."""
    config = {
        "cells": 48,
        "steps": 100,
        "cfl": 0.21,
        "gravity": 9.81,
        "slope": 0.0,
        "hump": 0.15,
        "dry_floor": 0.0017,
        "checkpoint_interval": 0,
        "restart": "false",
    }
    out, _ = run_case(tmp_path, "periodic_reference", **config)
    assert not (tmp_path / "periodic_reference.chk").exists()
    actual = load_csv(out)
    expected = reference_state(**{key: value for key, value in config.items() if key != "checkpoint_interval" and key != "restart"})
    clamped = reference_state(
        **{key: value for key, value in config.items() if key != "checkpoint_interval" and key != "restart"}, periodic=False
    )
    boundary_gap = max(
        max(abs(a[key] - b[key]) for key in ("h_avg", "h_slope", "q_avg", "q_slope"))
        for a, b in zip(expected, clamped)
    )
    assert boundary_gap > 1e-2, "the reference case must distinguish periodic from clamped boundaries"
    assert_same_state(actual, expected, tol=2e-12)


def test_dry_cells_have_zero_momentum_slope(tmp_path):
    """Cells clamped to the dry floor must have both momentum fields reset."""
    out, _ = run_case(
        tmp_path,
        "dry_momentum",
        cells=48,
        steps=3,
        cfl=0.18,
        gravity=9.81,
        slope=0.0,
        hump=-0.04,
        dry_floor=0.01,
        checkpoint_interval=0,
        restart="false",
    )
    rows = load_csv(out)
    dry_rows = [row for row in rows if abs(row["h_avg"] - 0.01) <= 1e-12]
    assert dry_rows, "expected at least one cell clamped to the dry floor"
    for row in dry_rows:
        assert row["q_avg"] == 0.0
        assert row["q_slope"] == 0.0


def test_dry_checkpoint_rows_have_zero_momentum(tmp_path):
    """Dry cells written to checkpoint records must carry zero modal momentum."""
    _, checkpoint = run_case(
        tmp_path,
        "dry_checkpoint",
        cells=48,
        steps=3,
        cfl=0.18,
        gravity=9.81,
        slope=0.0,
        hump=-0.04,
        dry_floor=0.01,
        checkpoint_interval=3,
        restart="false",
    )
    numeric_rows = [line.split() for line in checkpoint.read_text().splitlines() if line and line[0].isdigit()]
    dry_rows = [parts for parts in numeric_rows if abs(float(parts[1]) - 0.01) <= 1e-12]
    assert dry_rows, "expected at least one dry checkpoint row"
    for parts in dry_rows:
        assert float(parts[3]) == 0.0
        assert float(parts[4]) == 0.0


def test_incompatible_checkpoint_does_not_replace_the_requested_mesh(tmp_path):
    """A checkpoint for a different mesh must not change the result for the requested mesh."""
    coarse_common = {"cells": 36, "cfl": 0.16, "gravity": 9.81, "slope": 0.0, "hump": 0.032, "dry_floor": 0.002}
    _, foreign_checkpoint = run_case(tmp_path, "foreign", steps=18, checkpoint_interval=6, restart="false", **coarse_common)
    requested = {"cells": 60, "cfl": 0.16, "gravity": 9.81, "slope": 0.0, "hump": 0.032, "dry_floor": 0.002}
    reference_out, _ = run_case(tmp_path, "reference", steps=31, checkpoint_interval=0, restart="false", **requested)
    cfg = tmp_path / "request.cfg"
    restarted_out = tmp_path / "request.csv"
    write_config(cfg, steps=31, checkpoint_interval=4, restart="true", **requested)
    subprocess.run([str(BIN), str(cfg), str(restarted_out), str(foreign_checkpoint)], cwd=ROOT, check=True)
    assert_same_state(load_csv(reference_out), load_csv(restarted_out))


def test_checkpoint_stream_contains_complete_compatible_records(tmp_path):
    """Checkpoint records must carry all modal fields, expected times, and validating checksums."""
    _, checkpoint = run_case(
        tmp_path,
        "records",
        cells=32,
        steps=12,
        cfl=0.18,
        gravity=9.81,
        slope=0.0007,
        hump=0.028,
        dry_floor=0.001,
        checkpoint_interval=4,
        restart="false",
    )
    text = checkpoint.read_text().strip().splitlines()
    begins = [line for line in text if line.startswith("BEGIN DGSW1 ")]
    ends = [line for line in text if line.startswith("END ")]
    assert len(begins) == 3
    assert len(ends) == 3
    assert begins[-1].split()[3] == "12"
    numeric_rows = [line for line in text if line and line[0].isdigit()]
    assert len(numeric_rows) == 96
    assert all(len(line.split()) == 5 for line in numeric_rows)
    assert any(abs(float(line.split()[2])) > 1e-16 for line in numeric_rows)
    records = parse_checkpoint_records(checkpoint)
    assert [record["step"] for record in records] == [4, 8, 12]
    for record in records:
        assert record["time"] == pytest.approx(record["step"] * 0.18 / 32, abs=2e-16)
        expected_checksum = checkpoint_checksum(record["step"], record["time"], record["rows"])
        assert record["begin_checksum"] == pytest.approx(expected_checksum, rel=0, abs=2e-12)
        assert record["end_checksum"] == pytest.approx(record["begin_checksum"], rel=0, abs=0.0)


def test_restart_on_sloped_bed_preserves_all_modal_fields(tmp_path):
    """Restarting a forced run must keep modal height and momentum fields exactly aligned."""
    common = {"cells": 64, "cfl": 0.155, "gravity": 9.73, "slope": 0.0011, "hump": 0.035, "dry_floor": 0.0014}
    full_out, _ = run_case(tmp_path, "sloped_full", steps=47, checkpoint_interval=0, restart="false", **common)
    _, checkpoint = run_case(tmp_path, "sloped_seed", steps=29, checkpoint_interval=11, restart="false", **common)
    with checkpoint.open("a") as handle:
        handle.write("BEGIN DGSW1 64 31 0.0 1.0\n1 0.0 0.0 0.0 0.0\nEND 2.0\n")
    cfg = tmp_path / "sloped_resume.cfg"
    resumed_out = tmp_path / "sloped_resume.csv"
    write_config(cfg, steps=47, checkpoint_interval=6, restart="true", **common)
    subprocess.run([str(BIN), str(cfg), str(resumed_out), str(checkpoint)], cwd=ROOT, check=True)
    assert_same_state(load_csv(full_out), load_csv(resumed_out))
    records = [line.split()[3] for line in checkpoint.read_text().splitlines() if line.startswith("BEGIN DGSW1 ")]
    assert records[-1] == "47"


def test_restart_scans_mixed_checkpoint_stream_without_duplicate_append(tmp_path):
    """A restart must pick a later compatible record after an incompatible complete record."""
    common = {"cells": 52, "cfl": 0.142, "gravity": 9.81, "slope": 0.0003, "hump": 0.027, "dry_floor": 0.0011}
    expected_out, _ = run_case(tmp_path, "expected_mixed", steps=22, checkpoint_interval=0, restart="false", **common)
    _, mixed_checkpoint = run_case(tmp_path, "mixed_base", steps=12, checkpoint_interval=6, restart="false", **common)
    _, foreign_checkpoint = run_case(
        tmp_path,
        "mixed_foreign",
        cells=36,
        steps=8,
        cfl=0.142,
        gravity=9.81,
        slope=0.0003,
        hump=0.027,
        dry_floor=0.0011,
        checkpoint_interval=8,
        restart="false",
    )
    _, later_checkpoint = run_case(tmp_path, "mixed_later", steps=22, checkpoint_interval=22, restart="false", **common)
    mixed_checkpoint.write_text(
        mixed_checkpoint.read_text() + foreign_checkpoint.read_text() + later_checkpoint.read_text()
    )
    before = mixed_checkpoint.read_text()
    cfg = tmp_path / "mixed_resume.cfg"
    resumed_out = tmp_path / "mixed_resume.csv"
    write_config(cfg, steps=22, checkpoint_interval=5, restart="true", **common)
    subprocess.run([str(BIN), str(cfg), str(resumed_out), str(mixed_checkpoint)], cwd=ROOT, check=True)
    assert_same_state(load_csv(expected_out), load_csv(resumed_out))
    assert mixed_checkpoint.read_text() == before


def test_restart_skips_too_new_record_and_continues_scan(tmp_path):
    """A complete record beyond the target step must not stop scanning for a later usable record."""
    common = {"cells": 50, "cfl": 0.147, "gravity": 9.82, "slope": 0.00025, "hump": 0.029, "dry_floor": 0.0012}
    expected_out, _ = run_case(tmp_path, "expected_too_new", steps=22, checkpoint_interval=0, restart="false", **common)
    _, stream = run_case(tmp_path, "too_new_early", steps=8, checkpoint_interval=8, restart="false", **common)
    _, too_new = run_case(tmp_path, "too_new_future", steps=32, checkpoint_interval=32, restart="false", **common)
    _, later = run_case(tmp_path, "too_new_later", steps=18, checkpoint_interval=18, restart="false", **common)
    stream.write_text(stream.read_text() + too_new.read_text() + later.read_text())
    before_steps = checkpoint_steps(stream)
    cfg = tmp_path / "resume_too_new.cfg"
    resumed_out = tmp_path / "resume_too_new.csv"
    write_config(cfg, steps=22, checkpoint_interval=2, restart="true", **common)
    subprocess.run([str(BIN), str(cfg), str(resumed_out), str(stream)], cwd=ROOT, check=True)
    assert_same_state(load_csv(expected_out), load_csv(resumed_out))
    assert checkpoint_steps(stream) == before_steps + [20, 22]


def test_restart_skips_bad_checksum_and_continues_scan(tmp_path):
    """A checksum mismatch must invalidate one record without hiding later compatible records."""
    common = {"cells": 54, "cfl": 0.149, "gravity": 9.78, "slope": 0.00035, "hump": 0.031, "dry_floor": 0.00125}
    expected_out, _ = run_case(tmp_path, "expected_bad_checksum", steps=24, checkpoint_interval=0, restart="false", **common)
    _, stream = run_case(tmp_path, "bad_checksum_early", steps=8, checkpoint_interval=8, restart="false", **common)
    _, later = run_case(tmp_path, "bad_checksum_later", steps=20, checkpoint_interval=20, restart="false", **common)
    bad_rows = [(9.0, 0.0, 3.0, 0.0) for _ in range(common["cells"])]
    bad_record = make_checkpoint_record(common["cells"], 24, 24 * common["cfl"] / common["cells"], bad_rows, 0.0)
    stream.write_text(stream.read_text() + bad_record + later.read_text())
    before_steps = checkpoint_steps(stream)
    cfg = tmp_path / "resume_bad_checksum.cfg"
    resumed_out = tmp_path / "resume_bad_checksum.csv"
    write_config(cfg, steps=24, checkpoint_interval=4, restart="true", **common)
    subprocess.run([str(BIN), str(cfg), str(resumed_out), str(stream)], cwd=ROOT, check=True)
    assert_same_state(load_csv(expected_out), load_csv(resumed_out))
    assert checkpoint_steps(stream) == before_steps + [24]


def test_restart_skips_wrong_time_record_with_matching_checksum(tmp_path):
    """A record whose time disagrees with its step must not be chosen even when its checksum matches."""
    common = {"cells": 58, "cfl": 0.146, "gravity": 9.80, "slope": 0.00032, "hump": 0.030, "dry_floor": 0.00115}
    expected_out, _ = run_case(tmp_path, "expected_wrong_time", steps=24, checkpoint_interval=0, restart="false", **common)
    _, stream = run_case(tmp_path, "wrong_time_seed", steps=10, checkpoint_interval=10, restart="false", **common)
    _, later = run_case(tmp_path, "wrong_time_later", steps=18, checkpoint_interval=18, restart="false", **common)
    bad_time = 24 * common["cfl"] / common["cells"] + 0.25
    bad_rows = [(0.031 + 0.0001 * i, 0.0, 0.002, 0.0) for i in range(common["cells"])]
    bad_checksum = checkpoint_checksum(24, bad_time, bad_rows)
    bad_record = make_checkpoint_record(common["cells"], 24, bad_time, bad_rows, bad_checksum)
    stream.write_text(stream.read_text() + bad_record + later.read_text())
    before_steps = checkpoint_steps(stream)
    cfg = tmp_path / "resume_wrong_time.cfg"
    resumed_out = tmp_path / "resume_wrong_time.csv"
    write_config(cfg, steps=24, checkpoint_interval=3, restart="true", **common)
    subprocess.run([str(BIN), str(cfg), str(resumed_out), str(stream)], cwd=ROOT, check=True)
    assert_same_state(load_csv(expected_out), load_csv(resumed_out))
    assert checkpoint_steps(stream) == before_steps + [21, 24]


def test_restart_skips_corrupt_record_before_later_valid_state(tmp_path):
    """A corrupt compatible record must not stop the scan before a later usable state."""
    common = {"cells": 56, "cfl": 0.151, "gravity": 9.79, "slope": 0.0004, "hump": 0.026, "dry_floor": 0.0013}
    expected_out, _ = run_case(tmp_path, "expected_corrupt", steps=28, checkpoint_interval=0, restart="false", **common)
    _, stream = run_case(tmp_path, "early_corrupt", steps=14, checkpoint_interval=7, restart="false", **common)
    _, later = run_case(tmp_path, "later_corrupt", steps=28, checkpoint_interval=28, restart="false", **common)
    bad_rows = [f"{i} 0.0 0.0 0.0 0.0" for i in range(1, 57)]
    bad_rows[12] = "17 0.0 0.0 0.0 0.0"
    corrupt = "BEGIN DGSW1 56 21 0.0 0.0\n" + "\n".join(bad_rows) + "\nEND 0.0\n"
    stream.write_text(stream.read_text() + corrupt + later.read_text())
    before = stream.read_text()
    cfg = tmp_path / "resume_corrupt.cfg"
    resumed_out = tmp_path / "resume_corrupt.csv"
    write_config(cfg, steps=28, checkpoint_interval=5, restart="true", **common)
    subprocess.run([str(BIN), str(cfg), str(resumed_out), str(stream)], cwd=ROOT, check=True)
    assert_same_state(load_csv(expected_out), load_csv(resumed_out))
    assert stream.read_text() == before


def test_non_restart_run_replaces_existing_checkpoint_stream(tmp_path):
    """A fresh run must not append its checkpoints after stale records at the same output path."""
    checkpoint = tmp_path / "fresh.chk"
    checkpoint.write_text("BEGIN DGSW1 48 999 0.0 0.0\n1 0.0 0.0 0.0 0.0\nEND 0.0\n")
    cfg = tmp_path / "fresh.cfg"
    out = tmp_path / "fresh.csv"
    write_config(
        cfg,
        cells=48,
        steps=11,
        cfl=0.18,
        gravity=9.81,
        slope=0.0001,
        hump=0.024,
        dry_floor=0.0014,
        checkpoint_interval=5,
        restart="false",
    )
    subprocess.run([str(BIN), str(cfg), str(out), str(checkpoint)], cwd=ROOT, check=True)
    assert checkpoint_steps(checkpoint) == [5, 10, 11]


@pytest.mark.parametrize(
    "field,value",
    [
        ("cells", 23),
        ("cells", 97),
        ("steps", -1),
        ("cfl", 0.0),
        ("cfl", 0.2401),
        ("dry_floor", -0.001),
        ("checkpoint_interval", -1),
    ],
    ids=["cells-low", "cells-high", "steps-negative", "cfl-zero", "cfl-high", "dry-floor-negative", "interval-negative"],
)
def test_invalid_config_is_rejected_before_writing_files(tmp_path, field, value):
    """Each invalid run parameter must fail without leaving partial solver artifacts."""
    cfg = tmp_path / f"bad_{field}_{str(value).replace('-', 'neg').replace('.', '_')}.cfg"
    out = tmp_path / f"bad_{field}.csv"
    checkpoint = tmp_path / f"bad_{field}.chk"
    config = base_config(**{field: value})
    write_config(cfg, **config)
    result = run_raw(cfg, out, checkpoint)
    assert result.returncode != 0
    assert not out.exists()
    assert not checkpoint.exists()


@pytest.mark.parametrize(
    "name,overrides",
    [
        ("min_cells", {"cells": 24}),
        ("max_cells", {"cells": 96}),
        ("max_cfl", {"cfl": 0.24}),
    ],
)
def test_boundary_valid_configurations_are_accepted(tmp_path, name, overrides):
    """Boundary-valid cells and CFL values must run and emit state and checkpoints."""
    out, checkpoint = run_case(tmp_path, name, **base_config(**overrides))
    rows = load_csv(out)
    assert len(rows) == base_config(**overrides)["cells"]
    assert checkpoint.exists()


def test_final_checkpoint_is_written_when_interval_misses_last_step(tmp_path):
    """The checkpoint stream must include the requested final step even off interval."""
    _, checkpoint = run_case(
        tmp_path,
        "off_interval",
        cells=40,
        steps=10,
        cfl=0.19,
        gravity=9.81,
        slope=0.0,
        hump=0.023,
        dry_floor=0.0015,
        checkpoint_interval=4,
        restart="false",
    )
    begins = [line.split() for line in checkpoint.read_text().splitlines() if line.startswith("BEGIN DGSW1 ")]
    assert [int(parts[3]) for parts in begins] == [4, 8, 10]
