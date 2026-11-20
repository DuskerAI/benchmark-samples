from __future__ import annotations

import math
import subprocess
from pathlib import Path

import pytest

APP = Path("/app/orbitsim")
EXE = APP / "orbitsim"


@pytest.fixture(scope="session", autouse=True)
def built_tool() -> None:
    """Build the orbit integration executable once for all behavioral checks."""
    subprocess.run(["make"], cwd=APP, check=True, text=True, capture_output=True)
    assert EXE.exists(), "orbitsim executable was not produced"


def write_case(
    path: Path,
    *,
    nsteps: int,
    dt: float,
    output_every: int,
    bodies: list[tuple[float, float, float, float, float]],
    g: float = 1.0,
    epoch: tuple[int, float] | None = None,
    substeps: int | None = None,
    recenter_every: int | None = None,
    tidal_points: list[tuple[float, float, float]] | None = None,
    elastic_walls: tuple[float, float, float, float] | None = None,
    merge_radius: float | None = None,
    drag_zones: list[tuple[float, float, float, float, float]] | None = None,
    mass_changes: list[tuple[int, int, float]] | None = None,
    burns: list[tuple[int, int, float, float]] | None = None,
) -> None:
    lines = [
        f"nsteps {nsteps}",
        f"dt {dt:.17g}",
        f"g {g:.17g}",
        f"output_every {output_every}",
    ]
    if epoch is not None:
        lines.append(f"epoch {epoch[0]} {epoch[1]:.17g}")
    if substeps is not None:
        lines.append(f"substeps {substeps}")
    if recenter_every is not None:
        lines.append(f"recenter_every {recenter_every}")
    if tidal_points is not None:
        lines.append(f"tidal_points {len(tidal_points)}")
        lines.extend(" ".join(f"{value:.17g}" for value in row) for row in tidal_points)
    if elastic_walls is not None:
        lines.append("elastic_walls " + " ".join(f"{value:.17g}" for value in elastic_walls))
    if merge_radius is not None:
        lines.append(f"merge_radius {merge_radius:.17g}")
    if drag_zones is not None:
        lines.append(f"drag_zones {len(drag_zones)}")
        lines.extend(" ".join(f"{value:.17g}" for value in row) for row in drag_zones)
    lines.append(f"bodies {len(bodies)}")
    lines.extend(" ".join(f"{value:.17g}" for value in row) for row in bodies)
    if mass_changes is not None:
        lines.append(f"mass_changes {len(mass_changes)}")
        lines.extend(" ".join([str(step), str(body), f"{factor:.17g}"]) for step, body, factor in mass_changes)
    if burns is not None:
        lines.append(f"burns {len(burns)}")
        lines.extend(" ".join([str(step), str(body), f"{dvx:.17g}", f"{dvy:.17g}"]) for step, body, dvx, dvy in burns)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_tool(case_path: Path, outdir: Path, *extra: str) -> None:
    result = subprocess.run(
        [str(EXE), str(case_path), str(outdir), *extra],
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def read_final(path: Path) -> dict[int, tuple[float, float, float, float, float]]:
    rows: dict[int, tuple[float, float, float, float, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split()
        rows[int(parts[0])] = tuple(float(x) for x in parts[1:])  # type: ignore[assignment]
    return rows


def read_invariants(path: Path) -> dict[int, tuple[float, float, float]]:
    rows: dict[int, tuple[float, float, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split()
        rows[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
    return rows


def read_trajectory(path: Path) -> dict[int, list[tuple[int, float, float, float, float, float]]]:
    rows: dict[int, list[tuple[int, float, float, float, float, float]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split()
        step = int(parts[0])
        body = int(parts[2])
        values = tuple(float(x) for x in parts[3:])
        rows.setdefault(step, []).append((body, *values))  # type: ignore[arg-type]
    return rows


def read_checkpoint_header(path: Path) -> tuple[int, float, int]:
    parts = path.read_text(encoding="utf-8").splitlines()[0].split()
    return int(parts[0]), float(parts[1]), int(parts[2])


def read_checkpoint(path: Path) -> tuple[int, float, list[tuple[float, float, float, float, float]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    step, time, nbody = read_checkpoint_header(path)
    bodies = [tuple(float(x) for x in line.split()) for line in lines[1:]]
    assert len(bodies) == nbody
    return step, time, bodies  # type: ignore[return-value]


def write_checkpoint(
    path: Path,
    step: int,
    time: float,
    bodies: list[tuple[float, float, float, float, float]],
) -> None:
    lines = [f"{step} {time:.17g} {len(bodies)}"]
    lines.extend(" ".join(f"{value:.17g}" for value in row) for row in bodies)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def max_state_delta(a: dict[int, tuple[float, ...]], b: dict[int, tuple[float, ...]]) -> float:
    assert a.keys() == b.keys()
    return max(abs(x - y) for key in a for x, y in zip(a[key], b[key]))


def independent_invariants(
    rows: list[tuple[int, float, float, float, float, float]],
    g: float = 1.0,
    tidal_points: list[tuple[float, float, float]] | None = None,
) -> tuple[float, float]:
    ordered = sorted(rows)
    energy = 0.0
    angular = 0.0
    for _, mass, x, y, vx, vy in ordered:
        energy += 0.5 * mass * (vx * vx + vy * vy)
        angular += mass * (x * vy - y * vx)
    for i, (_, mi, xi, yi, _, _) in enumerate(ordered[:-1]):
        for _, mj, xj, yj, _, _ in ordered[i + 1 :]:
            radius = math.hypot(xi - xj, yi - yj)
            energy -= g * mi * mj / radius
    for _, mass, x, y, _, _ in ordered:
        for px, py, pmass in tidal_points or []:
            radius = math.hypot(x - px, y - py)
            energy -= g * mass * pmass / radius
    return energy, angular


def center_of_mass(rows: list[tuple[int, float, float, float, float, float]]) -> tuple[float, float, float, float]:
    total_mass = sum(row[1] for row in rows)
    cx = sum(mass * x for _, mass, x, _, _, _ in rows) / total_mass
    cy = sum(mass * y for _, mass, _, y, _, _ in rows) / total_mass
    cvx = sum(mass * vx for _, mass, _, _, vx, _ in rows) / total_mass
    cvy = sum(mass * vy for _, mass, _, _, _, vy in rows) / total_mass
    return cx, cy, cvx, cvy


def reference_trajectory(
    *,
    start_step: int,
    start_time: float,
    nsteps: int,
    dt: float,
    output_every: int,
    bodies: list[tuple[float, float, float, float, float]],
    g: float,
    substeps: int = 1,
    recenter_every: int | None = None,
    tidal_points: list[tuple[float, float, float]] | None = None,
    elastic_walls: tuple[float, float, float, float] | None = None,
    merge_radius: float | None = None,
    drag_zones: list[tuple[float, float, float, float, float]] | None = None,
    mass_changes: list[tuple[int, int, float]] | None = None,
    burns: list[tuple[int, int, float, float]] | None = None,
) -> tuple[
    dict[int, list[tuple[int, float, float, float, float, float]]],
    dict[int, tuple[float, float, float]],
    dict[int, tuple[float, float, float, float, float]],
]:
    """Integrate the stated Newtonian contract independently of the submitted code."""
    state = [list(row) for row in bodies]
    by_step: dict[int, list[tuple[int, float, float]]] = {}
    for step, body, dvx, dvy in burns or []:
        by_step.setdefault(step, []).append((body, dvx, dvy))
    mass_by_step: dict[int, list[tuple[int, float]]] = {}
    for step, body, factor in mass_changes or []:
        mass_by_step.setdefault(step, []).append((body, factor))
    trajectory: dict[int, list[tuple[int, float, float, float, float, float]]] = {}
    invariants: dict[int, tuple[float, float, float]] = {}

    def accelerations() -> list[tuple[float, float]]:
        acc: list[tuple[float, float]] = []
        for i, (_, xi, yi, _, _) in enumerate(state):
            ax = 0.0
            ay = 0.0
            for j, (mj, xj, yj, _, _) in enumerate(state):
                if i == j:
                    continue
                dx = xj - xi
                dy = yj - yi
                r2 = dx * dx + dy * dy
                invr3 = 1.0 / (r2 * math.sqrt(r2))
                ax += g * mj * dx * invr3
                ay += g * mj * dy * invr3
            for px, py, pmass in tidal_points or []:
                dx = px - xi
                dy = py - yi
                r2 = dx * dx + dy * dy
                invr3 = 1.0 / (r2 * math.sqrt(r2))
                ax += g * pmass * dx * invr3
                ay += g * pmass * dy * invr3
            acc.append((ax, ay))
        return acc

    def recenter() -> None:
        rows = [(idx + 1, *row) for idx, row in enumerate(state)]
        cx, cy, cvx, cvy = center_of_mass(rows)  # type: ignore[arg-type]
        for row in state:
            row[1] -= cx
            row[2] -= cy
            row[3] -= cvx
            row[4] -= cvy

    def reflect_walls() -> None:
        if elastic_walls is None:
            return
        xmin, xmax, ymin, ymax = elastic_walls
        for row in state:
            while row[1] < xmin or row[1] > xmax:
                if row[1] < xmin:
                    row[1] = 2.0 * xmin - row[1]
                else:
                    row[1] = 2.0 * xmax - row[1]
                row[3] = -row[3]
            while row[2] < ymin or row[2] > ymax:
                if row[2] < ymin:
                    row[2] = 2.0 * ymin - row[2]
                else:
                    row[2] = 2.0 * ymax - row[2]
                row[4] = -row[4]

    def merge_close_bodies() -> None:
        if merge_radius is None:
            return
        changed = True
        while changed:
            changed = False
            for i in range(len(state)):
                for j in range(i + 1, len(state)):
                    mi, xi, yi, vxi, vyi = state[i]
                    mj, xj, yj, vxj, vyj = state[j]
                    if math.hypot(xi - xj, yi - yj) <= merge_radius:
                        mt = mi + mj
                        state[i] = [
                            mt,
                            (mi * xi + mj * xj) / mt,
                            (mi * yi + mj * yj) / mt,
                            (mi * vxi + mj * vxj) / mt,
                            (mi * vyi + mj * vyj) / mt,
                        ]
                        del state[j]
                        changed = True
                        break
                if changed:
                        break

    def apply_drag(h: float) -> None:
        for row in state:
            _, x, y, _, _ = row
            for xmin, xmax, ymin, ymax, gamma in drag_zones or []:
                if xmin <= x <= xmax and ymin <= y <= ymax:
                    scale = math.exp(-gamma * h)
                    row[3] *= scale
                    row[4] *= scale

    def record(step: int, time: float) -> None:
        rows = [(idx + 1, *row) for idx, row in enumerate(state)]
        trajectory[step] = [tuple(row) for row in rows]  # type: ignore[list-item]
        energy, angular = independent_invariants(trajectory[step], g, tidal_points)
        invariants[step] = (time, energy, angular)

    step = start_step
    time = start_time
    record(step, time)
    while step < nsteps:
        for body, factor in mass_by_step.get(step + 1, []):
            state[body - 1][0] *= factor
        for body, dvx, dvy in by_step.get(step + 1, []):
            state[body - 1][3] += dvx
            state[body - 1][4] += dvy
        micro_dt = dt / substeps
        for _ in range(substeps):
            acc = accelerations()
            for row, (ax, ay) in zip(state, acc):
                row[3] += 0.5 * micro_dt * ax
                row[4] += 0.5 * micro_dt * ay
                row[1] += micro_dt * row[3]
                row[2] += micro_dt * row[4]
            acc = accelerations()
            for row, (ax, ay) in zip(state, acc):
                row[3] += 0.5 * micro_dt * ax
                row[4] += 0.5 * micro_dt * ay
            apply_drag(micro_dt)
        step += 1
        time += dt
        reflect_walls()
        merge_close_bodies()
        if recenter_every is not None and step % recenter_every == 0:
            recenter()
        if step % output_every == 0 or step == nsteps:
            record(step, time)

    final = {idx + 1: tuple(row) for idx, row in enumerate(state)}
    return trajectory, invariants, final  # type: ignore[return-value]


def test_resume_matches_uninterrupted_arc(tmp_path: Path) -> None:
    """A split integration must land on the same final trajectory as the uninterrupted arc."""
    case_path = tmp_path / "arc.case"
    write_case(
        case_path,
        nsteps=430,
        dt=0.0017,
        output_every=40,
        bodies=[
            (1.0, -0.520, 0.0, 0.0, -0.6740),
            (0.95, 0.480, 0.0, 0.0, 0.7095),
            (0.0025, 0.0, 1.31, -0.402, 0.018),
        ],
    )
    direct = tmp_path / "direct"
    part = tmp_path / "part"
    resumed = tmp_path / "resumed"

    run_tool(case_path, direct)
    run_tool(case_path, part, "--checkpoint-at", "217")
    assert (part / "checkpoint.dat").exists()
    assert 217 in read_invariants(part / "invariants.tsv")
    run_tool(case_path, resumed, "--resume", str(part / "checkpoint.dat"))

    assert max_state_delta(read_final(direct / "final_state.tsv"), read_final(resumed / "final_state.tsv")) < 2.0e-10
    direct_inv = read_invariants(direct / "invariants.tsv")[430]
    resumed_inv = read_invariants(resumed / "invariants.tsv")[430]
    assert max(abs(a - b) for a, b in zip(direct_inv, resumed_inv)) < 2.0e-10


def test_initial_checkpoint_is_a_valid_start(tmp_path: Path) -> None:
    """A checkpoint written at the initial state must resume to the same endpoint as a fresh run."""
    case_path = tmp_path / "seed.case"
    write_case(
        case_path,
        nsteps=180,
        dt=0.0025,
        output_every=45,
        bodies=[
            (1.2, -0.43, 0.08, -0.04, -0.830),
            (0.8, 0.64, -0.12, 0.06, 0.705),
            (0.0017, 0.22, -1.08, 0.585, 0.075),
        ],
    )
    direct = tmp_path / "fresh"
    seed = tmp_path / "seed"
    resumed = tmp_path / "from_seed"

    run_tool(case_path, direct)
    run_tool(case_path, seed, "--checkpoint-at", "0")
    assert 0 in read_invariants(seed / "invariants.tsv")
    run_tool(case_path, resumed, "--resume", str(seed / "checkpoint.dat"))

    assert max_state_delta(read_final(direct / "final_state.tsv"), read_final(resumed / "final_state.tsv")) < 2.0e-10


def test_repeated_checkpoint_chain_matches_fresh_near_passage(tmp_path: Path) -> None:
    """Multiple absolute checkpoints must compose to the same Newtonian arc."""
    case_path = tmp_path / "chain.case"
    write_case(
        case_path,
        nsteps=512,
        dt=0.00078,
        output_every=64,
        g=0.73,
        bodies=[
            (1.05, -0.41, -0.16, 0.248, -0.692),
            (0.92, 0.47, 0.13, -0.218, 0.728),
            (0.014, -0.05, 0.63, -0.867, -0.038),
            (0.009, 0.18, -0.72, 0.744, 0.106),
            (0.006, -0.88, 0.31, -0.152, -0.407),
        ],
    )
    direct = tmp_path / "direct"
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    resumed = tmp_path / "resumed"

    run_tool(case_path, direct)
    run_tool(case_path, first, "--checkpoint-at", "73")
    run_tool(case_path, second, "--resume", str(first / "checkpoint.dat"), "--checkpoint-at", "211")
    step, time, nbody = read_checkpoint_header(second / "checkpoint.dat")
    assert (step, nbody) == (211, 5)
    assert abs(time - 211 * 0.00078) < 2.0e-13

    run_tool(case_path, third, "--resume", str(second / "checkpoint.dat"), "--checkpoint-at", "389")
    assert read_checkpoint_header(third / "checkpoint.dat")[0] == 389
    run_tool(case_path, resumed, "--resume", str(third / "checkpoint.dat"))

    assert max_state_delta(read_final(direct / "final_state.tsv"), read_final(resumed / "final_state.tsv")) < 2.5e-10
    direct_inv = read_invariants(direct / "invariants.tsv")
    resumed_inv = read_invariants(resumed / "invariants.tsv")
    assert max(abs(a - b) for a, b in zip(direct_inv[512], resumed_inv[512])) < 2.5e-10

    first_energy = direct_inv[0][1]
    final_energy = direct_inv[512][1]
    assert abs(final_energy - first_energy) / max(1.0, abs(first_energy)) < 5.0e-8


def test_reported_invariants_match_recorded_states(tmp_path: Path) -> None:
    """Reported conserved quantities must agree with the states written for each sampled step."""
    case_path = tmp_path / "conserved.case"
    write_case(
        case_path,
        nsteps=260,
        dt=0.0012,
        output_every=37,
        bodies=[
            (1.0, -0.50, 0.0, 0.0, -0.7060),
            (1.0, 0.50, 0.0, 0.0, 0.7060),
            (0.003, 0.0, 1.42, -0.368, 0.0),
            (0.002, 0.0, -1.58, 0.331, 0.0),
        ],
    )
    outdir = tmp_path / "out"
    run_tool(case_path, outdir)

    trajectory = read_trajectory(outdir / "trajectory.tsv")
    invariants = read_invariants(outdir / "invariants.tsv")
    assert set(invariants).issubset(trajectory)
    for step, (_, energy, angular) in invariants.items():
        ref_energy, ref_angular = independent_invariants(trajectory[step])
        assert abs(energy - ref_energy) < 2.0e-11
        assert abs(angular - ref_angular) < 2.0e-11

    first_energy = invariants[0][1]
    final_energy = invariants[260][1]
    assert abs(final_energy - first_energy) / max(1.0, abs(first_energy)) < 5.0e-8


def test_burns_recenter_and_checkpoint_chain_use_absolute_steps(tmp_path: Path) -> None:
    """Impulses and COM recentering must occur at absolute steps across restart chains."""
    bodies = [
        (1.40, -0.56, 0.09, 0.018, -0.508),
        (0.85, 0.62, -0.18, -0.041, 0.782),
        (0.11, -0.18, 0.74, -0.614, -0.214),
        (0.07, 0.37, -0.81, 0.531, 0.188),
    ]
    burns = [(23, 3, 0.0065, -0.0040), (72, 4, -0.0030, 0.0055), (118, 2, 0.0018, -0.0022)]
    case_path = tmp_path / "events.case"
    no_burn_case = tmp_path / "no_burn.case"
    write_case(
        case_path,
        nsteps=170,
        dt=0.00092,
        output_every=34,
        g=0.87,
        recenter_every=17,
        bodies=bodies,
        burns=burns,
    )
    write_case(
        no_burn_case,
        nsteps=170,
        dt=0.00092,
        output_every=34,
        g=0.87,
        recenter_every=17,
        bodies=bodies,
        burns=[],
    )

    direct = tmp_path / "direct"
    no_burn = tmp_path / "no_burn"
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    resumed = tmp_path / "resumed"

    run_tool(case_path, direct)
    run_tool(no_burn_case, no_burn)
    assert max_state_delta(read_final(direct / "final_state.tsv"), read_final(no_burn / "final_state.tsv")) > 1.0e-3

    run_tool(case_path, first, "--checkpoint-at", "39")
    run_tool(case_path, second, "--resume", str(first / "checkpoint.dat"), "--checkpoint-at", "91")
    run_tool(case_path, third, "--resume", str(second / "checkpoint.dat"), "--checkpoint-at", "119")
    assert read_checkpoint_header(third / "checkpoint.dat")[0] == 119
    run_tool(case_path, resumed, "--resume", str(third / "checkpoint.dat"))

    assert max_state_delta(read_final(direct / "final_state.tsv"), read_final(resumed / "final_state.tsv")) < 2.5e-10
    direct_traj = read_trajectory(direct / "trajectory.tsv")
    for step in (34, 68, 102, 136, 170):
        assert step in direct_traj
        assert max(abs(value) for value in center_of_mass(direct_traj[step])) < 5.0e-13
    resumed_inv = read_invariants(resumed / "invariants.tsv")[170]
    direct_inv = read_invariants(direct / "invariants.tsv")[170]
    assert max(abs(a - b) for a, b in zip(direct_inv, resumed_inv)) < 2.5e-10


def test_epoch_chain_matches_independent_absolute_reference(tmp_path: Path) -> None:
    """Epoch starts, duplicate burns, and recenter cadence must follow absolute steps across checkpoints."""
    bodies = [
        (1.30, -0.72, 0.16, 0.038, -0.612),
        (0.77, 0.58, -0.23, -0.071, 0.804),
        (0.18, -0.11, 0.94, -0.704, -0.119),
        (0.05, 0.43, -1.08, 0.493, 0.203),
        (0.032, -1.21, -0.37, 0.176, -0.278),
    ]
    burns = [
        (149, 4, -0.0024, 0.0042),
        (116, 2, 0.0017, -0.0021),
        (149, 1, 0.0031, 0.0013),
        (173, 5, -0.0012, -0.0037),
        (116, 2, -0.0004, 0.0015),
    ]
    case_path = tmp_path / "epoch.case"
    write_case(
        case_path,
        nsteps=188,
        dt=0.00083,
        output_every=17,
        g=0.91,
        epoch=(104, 0.375),
        recenter_every=23,
        bodies=bodies,
        burns=burns,
    )
    direct = tmp_path / "direct"
    first = tmp_path / "first"
    second = tmp_path / "second"
    resumed = tmp_path / "resumed"

    run_tool(case_path, direct)
    run_tool(case_path, first, "--checkpoint-at", "137")
    step, time, nbody = read_checkpoint_header(first / "checkpoint.dat")
    assert (step, nbody) == (137, 5)
    assert abs(time - (0.375 + (137 - 104) * 0.00083)) < 2.0e-13
    run_tool(case_path, second, "--resume", str(first / "checkpoint.dat"), "--checkpoint-at", "163")
    run_tool(case_path, resumed, "--resume", str(second / "checkpoint.dat"))

    ref_traj, ref_inv, ref_final = reference_trajectory(
        start_step=104,
        start_time=0.375,
        nsteps=188,
        dt=0.00083,
        output_every=17,
        g=0.91,
        recenter_every=23,
        bodies=bodies,
        burns=burns,
    )
    assert max_state_delta(read_final(direct / "final_state.tsv"), ref_final) < 2.0e-10
    assert max_state_delta(read_final(resumed / "final_state.tsv"), ref_final) < 2.0e-10
    direct_traj = read_trajectory(direct / "trajectory.tsv")
    for step in (104, 119, 136, 153, 170, 187, 188):
        assert step in direct_traj
        observed = {body: row for body, *row in direct_traj[step]}
        expected = {body: row for body, *row in ref_traj[step]}
        assert max_state_delta(observed, expected) < 2.0e-10
        direct_inv = read_invariants(direct / "invariants.tsv")[step]
        assert max(abs(a - b) for a, b in zip(direct_inv, ref_inv[step])) < 2.0e-10


def test_resume_uses_checkpoint_body_state_as_authoritative(tmp_path: Path) -> None:
    """A resume must continue from the checkpoint contents rather than reconstructing from CASE."""
    bodies = [
        (1.05, -0.39, 0.07, -0.018, -0.731),
        (0.93, 0.52, -0.11, 0.044, 0.762),
        (0.021, -0.19, 0.88, -0.621, -0.064),
        (0.013, 0.31, -0.97, 0.557, 0.091),
    ]
    burns = [(64, 3, 0.0043, -0.0018), (97, 1, -0.0025, 0.0029), (122, 4, 0.0017, 0.0033)]
    case_path = tmp_path / "mutated.case"
    write_case(
        case_path,
        nsteps=146,
        dt=0.00105,
        output_every=29,
        g=0.82,
        recenter_every=31,
        bodies=bodies,
        burns=burns,
    )
    direct = tmp_path / "direct"
    seed = tmp_path / "seed"
    resumed = tmp_path / "resumed"

    run_tool(case_path, direct)
    run_tool(case_path, seed, "--checkpoint-at", "76")
    step, time, checkpoint_bodies = read_checkpoint(seed / "checkpoint.dat")
    mutated = list(checkpoint_bodies)
    m0, x0, y0, vx0, vy0 = mutated[0]
    mutated[0] = (m0, x0 + 0.006, y0 - 0.004, vx0 + 0.018, vy0 - 0.011)
    m2, x2, y2, vx2, vy2 = mutated[2]
    mutated[2] = (m2, x2 - 0.0035, y2 + 0.0025, vx2 - 0.013, vy2 + 0.015)
    write_checkpoint(seed / "checkpoint.dat", step, time, mutated)
    run_tool(case_path, resumed, "--resume", str(seed / "checkpoint.dat"))

    _, ref_inv, ref_final = reference_trajectory(
        start_step=step,
        start_time=time,
        nsteps=146,
        dt=0.00105,
        output_every=29,
        g=0.82,
        recenter_every=31,
        bodies=mutated,
        burns=burns,
    )
    assert max_state_delta(read_final(resumed / "final_state.tsv"), ref_final) < 2.0e-10
    assert max_state_delta(read_final(direct / "final_state.tsv"), ref_final) > 2.0e-4
    resumed_inv = read_invariants(resumed / "invariants.tsv")[146]
    assert max(abs(a - b) for a, b in zip(resumed_inv, ref_inv[146])) < 2.0e-10


def test_substeps_and_tidal_points_match_independent_reference(tmp_path: Path) -> None:
    """Subcycled steps under fixed tidal masses must match the published integration contract."""
    bodies = [
        (1.18, -0.44, 0.12, 0.031, -0.705),
        (0.86, 0.55, -0.16, -0.052, 0.756),
        (0.064, -0.25, 0.78, -0.579, -0.143),
        (0.041, 0.36, -0.91, 0.512, 0.166),
    ]
    tidal = [(-1.35, 0.42, 0.23), (1.10, -0.68, 0.17)]
    burns = [(31, 3, 0.0036, -0.0021), (74, 1, -0.0018, 0.0027), (74, 4, 0.0024, -0.0031)]
    case_path = tmp_path / "tidal.case"
    write_case(
        case_path,
        nsteps=132,
        dt=0.0021,
        output_every=11,
        g=0.64,
        substeps=5,
        recenter_every=22,
        tidal_points=tidal,
        bodies=bodies,
        burns=burns,
    )
    outdir = tmp_path / "out"
    run_tool(case_path, outdir)

    ref_traj, ref_inv, ref_final = reference_trajectory(
        start_step=0,
        start_time=0.0,
        nsteps=132,
        dt=0.0021,
        output_every=11,
        g=0.64,
        substeps=5,
        recenter_every=22,
        tidal_points=tidal,
        bodies=bodies,
        burns=burns,
    )
    assert max_state_delta(read_final(outdir / "final_state.tsv"), ref_final) < 2.5e-10
    trajectory = read_trajectory(outdir / "trajectory.tsv")
    invariants = read_invariants(outdir / "invariants.tsv")
    for step in (0, 11, 22, 55, 88, 121, 132):
        assert step in trajectory
        observed = {body: row for body, *row in trajectory[step]}
        expected = {body: row for body, *row in ref_traj[step]}
        assert max_state_delta(observed, expected) < 2.5e-10
        assert max(abs(a - b) for a, b in zip(invariants[step], ref_inv[step])) < 2.5e-10
    for step in (22, 44, 66, 88, 110, 132):
        assert max(abs(value) for value in center_of_mass(trajectory[step])) < 5.0e-13


def test_epoch_tidal_substep_restart_chain_preserves_absolute_semantics(tmp_path: Path) -> None:
    """Epoch restarts must retain absolute burns, substeps, and fixed-source energy terms."""
    bodies = [
        (1.42, -0.62, 0.21, 0.046, -0.587),
        (0.91, 0.71, -0.29, -0.083, 0.714),
        (0.19, -0.18, 1.03, -0.658, -0.097),
        (0.06, 0.49, -1.17, 0.481, 0.218),
        (0.033, -1.08, -0.44, 0.191, -0.244),
    ]
    tidal = [(-1.80, -0.35, 0.31), (1.48, 0.62, 0.27), (0.18, 1.74, 0.12)]
    burns = [(207, 5, -0.0014, 0.0035), (173, 2, 0.0022, -0.0016), (207, 1, 0.0028, 0.0011)]
    case_path = tmp_path / "epoch_tidal.case"
    write_case(
        case_path,
        nsteps=226,
        dt=0.00113,
        output_every=13,
        g=0.79,
        epoch=(151, 1.75),
        substeps=4,
        recenter_every=19,
        tidal_points=tidal,
        bodies=bodies,
        burns=burns,
    )
    direct = tmp_path / "direct"
    first = tmp_path / "first"
    second = tmp_path / "second"
    resumed = tmp_path / "resumed"

    run_tool(case_path, direct)
    run_tool(case_path, first, "--checkpoint-at", "181")
    assert read_checkpoint_header(first / "checkpoint.dat")[0] == 181
    run_tool(case_path, second, "--resume", str(first / "checkpoint.dat"), "--checkpoint-at", "209")
    step, time, bodies_from_checkpoint = read_checkpoint(second / "checkpoint.dat")
    assert step == 209
    assert abs(time - (1.75 + (209 - 151) * 0.00113)) < 2.0e-13
    run_tool(case_path, resumed, "--resume", str(second / "checkpoint.dat"))

    _, ref_inv, ref_final = reference_trajectory(
        start_step=step,
        start_time=time,
        nsteps=226,
        dt=0.00113,
        output_every=13,
        g=0.79,
        substeps=4,
        recenter_every=19,
        tidal_points=tidal,
        bodies=bodies_from_checkpoint,
        burns=burns,
    )
    assert max_state_delta(read_final(resumed / "final_state.tsv"), ref_final) < 2.5e-10
    assert max_state_delta(read_final(direct / "final_state.tsv"), read_final(resumed / "final_state.tsv")) < 2.5e-10
    resumed_inv = read_invariants(resumed / "invariants.tsv")[226]
    assert max(abs(a - b) for a, b in zip(resumed_inv, ref_inv[226])) < 2.5e-10


def test_elastic_wall_reflections_preserve_overshoot_across_restarts(tmp_path: Path) -> None:
    """Wall crossings must preserve overshoot and compose identically through checkpoints."""
    bodies = [
        (1.0, -0.18, 0.04, -0.52, 0.31),
        (0.7, 0.22, -0.08, 0.47, -0.36),
        (0.03, 0.03, 0.19, 0.16, 0.44),
    ]
    walls = (-0.32, 0.34, -0.30, 0.29)
    burns = [(9, 2, 0.08, -0.02), (21, 1, -0.05, 0.07), (33, 3, 0.02, -0.06)]
    case_path = tmp_path / "walls.case"
    write_case(
        case_path,
        nsteps=48,
        dt=0.018,
        output_every=6,
        g=0.015,
        substeps=3,
        tidal_points=[(0.75, -0.55, 0.02)],
        elastic_walls=walls,
        bodies=bodies,
        burns=burns,
    )
    direct = tmp_path / "direct"
    first = tmp_path / "first"
    second = tmp_path / "second"
    resumed = tmp_path / "resumed"

    run_tool(case_path, direct)
    run_tool(case_path, first, "--checkpoint-at", "17")
    assert 17 in read_trajectory(first / "trajectory.tsv")
    run_tool(case_path, second, "--resume", str(first / "checkpoint.dat"), "--checkpoint-at", "29")
    assert 29 in read_invariants(second / "invariants.tsv")
    run_tool(case_path, resumed, "--resume", str(second / "checkpoint.dat"))

    ref_traj, ref_inv, ref_final = reference_trajectory(
        start_step=0,
        start_time=0.0,
        nsteps=48,
        dt=0.018,
        output_every=6,
        g=0.015,
        substeps=3,
        tidal_points=[(0.75, -0.55, 0.02)],
        elastic_walls=walls,
        bodies=bodies,
        burns=burns,
    )
    assert max_state_delta(read_final(direct / "final_state.tsv"), ref_final) < 2.5e-10
    assert max_state_delta(read_final(resumed / "final_state.tsv"), ref_final) < 2.5e-10
    direct_traj = read_trajectory(direct / "trajectory.tsv")
    xmin, xmax, ymin, ymax = walls
    for step in (6, 12, 18, 24, 30, 36, 42, 48):
        observed = {body: row for body, *row in direct_traj[step]}
        expected = {body: row for body, *row in ref_traj[step]}
        assert max_state_delta(observed, expected) < 2.5e-10
        assert all(xmin <= row[2] <= xmax and ymin <= row[3] <= ymax for row in direct_traj[step])
        direct_inv = read_invariants(direct / "invariants.tsv")[step]
        assert max(abs(a - b) for a, b in zip(direct_inv, ref_inv[step])) < 2.5e-10


def test_epoch_walls_recenter_and_mutated_checkpoint_state_interact(tmp_path: Path) -> None:
    """Wall, epoch, recenter, and authoritative checkpoint state semantics must interact consistently."""
    bodies = [
        (1.1, -0.21, 0.11, -0.38, 0.27),
        (0.9, 0.24, -0.16, 0.33, -0.31),
        (0.04, -0.02, 0.25, 0.12, 0.42),
        (0.035, 0.13, -0.22, -0.17, -0.39),
    ]
    walls = (-0.43, 0.41, -0.38, 0.36)
    burns = [(58, 4, 0.04, 0.03), (61, 1, -0.03, 0.05), (79, 2, 0.02, -0.04)]
    case_path = tmp_path / "epoch_walls.case"
    write_case(
        case_path,
        nsteps=92,
        dt=0.0125,
        output_every=7,
        g=0.022,
        epoch=(51, 2.4),
        substeps=4,
        recenter_every=14,
        elastic_walls=walls,
        bodies=bodies,
        burns=burns,
    )
    seed = tmp_path / "seed"
    resumed = tmp_path / "resumed"

    run_tool(case_path, seed, "--checkpoint-at", "63")
    step, time, checkpoint_bodies = read_checkpoint(seed / "checkpoint.dat")
    mutated = list(checkpoint_bodies)
    mass, x, y, vx, vy = mutated[1]
    mutated[1] = (mass, x + 0.03, y - 0.02, vx - 0.11, vy + 0.09)
    write_checkpoint(seed / "checkpoint.dat", step, time, mutated)
    run_tool(case_path, resumed, "--resume", str(seed / "checkpoint.dat"))

    ref_traj, ref_inv, ref_final = reference_trajectory(
        start_step=step,
        start_time=time,
        nsteps=92,
        dt=0.0125,
        output_every=7,
        g=0.022,
        substeps=4,
        recenter_every=14,
        elastic_walls=walls,
        bodies=mutated,
        burns=burns,
    )
    assert max_state_delta(read_final(resumed / "final_state.tsv"), ref_final) < 2.5e-10
    trajectory = read_trajectory(resumed / "trajectory.tsv")
    for step in (63, 70, 77, 84, 91, 92):
        observed = {body: row for body, *row in trajectory[step]}
        expected = {body: row for body, *row in ref_traj[step]}
        assert max_state_delta(observed, expected) < 2.5e-10
        resumed_inv = read_invariants(resumed / "invariants.tsv")[step]
        assert max(abs(a - b) for a, b in zip(resumed_inv, ref_inv[step])) < 2.5e-10


def test_merge_radius_conserves_momentum_through_restart_chain(tmp_path: Path) -> None:
    """Close bodies must coalesce deterministically while preserving mass and momentum across restarts."""
    bodies = [
        (1.2, -0.08, 0.0, 0.034, 0.0),
        (0.8, 0.08, 0.0, -0.046, 0.0),
        (0.06, 0.0, 0.42, 0.0, -0.18),
        (0.04, 0.0, -0.39, 0.0, 0.21),
    ]
    burns = [(11, 3, 0.012, -0.008), (17, 3, -0.009, 0.006)]
    case_path = tmp_path / "merge.case"
    write_case(
        case_path,
        nsteps=36,
        dt=0.045,
        output_every=3,
        g=0.018,
        substeps=4,
        recenter_every=6,
        merge_radius=0.055,
        bodies=bodies,
        burns=burns,
    )
    direct = tmp_path / "direct"
    first = tmp_path / "first"
    second = tmp_path / "second"
    resumed = tmp_path / "resumed"

    run_tool(case_path, direct)
    run_tool(case_path, first, "--checkpoint-at", "13")
    run_tool(case_path, second, "--resume", str(first / "checkpoint.dat"), "--checkpoint-at", "24")
    step, time, checkpoint_bodies = read_checkpoint(second / "checkpoint.dat")
    run_tool(case_path, resumed, "--resume", str(second / "checkpoint.dat"))

    ref_traj, ref_inv, ref_final = reference_trajectory(
        start_step=0,
        start_time=0.0,
        nsteps=36,
        dt=0.045,
        output_every=3,
        g=0.018,
        substeps=4,
        recenter_every=6,
        merge_radius=0.055,
        bodies=bodies,
        burns=burns,
    )
    _, _, resumed_from_checkpoint = reference_trajectory(
        start_step=step,
        start_time=time,
        nsteps=36,
        dt=0.045,
        output_every=3,
        g=0.018,
        substeps=4,
        recenter_every=6,
        merge_radius=0.055,
        bodies=checkpoint_bodies,
        burns=burns,
    )
    assert len(read_final(direct / "final_state.tsv")) == 1
    assert max_state_delta(read_final(direct / "final_state.tsv"), ref_final) < 2.5e-10
    assert max_state_delta(read_final(resumed / "final_state.tsv"), resumed_from_checkpoint) < 2.5e-10
    assert max_state_delta(read_final(direct / "final_state.tsv"), read_final(resumed / "final_state.tsv")) < 2.5e-10
    trajectory = read_trajectory(direct / "trajectory.tsv")
    assert any(len(rows) == 1 for step, rows in trajectory.items() if step >= 12)
    for step in (12, 18, 24, 30, 36):
        observed = {body: row for body, *row in trajectory[step]}
        expected = {body: row for body, *row in ref_traj[step]}
        assert max_state_delta(observed, expected) < 2.5e-10
        direct_inv = read_invariants(direct / "invariants.tsv")[step]
        assert max(abs(a - b) for a, b in zip(direct_inv, ref_inv[step])) < 2.5e-10


def test_drag_zones_and_mass_changes_survive_mutated_restart_chain(tmp_path: Path) -> None:
    """Damping zones and mass-change events must compose with authoritative checkpoints."""
    bodies = [
        (1.10, -0.34, 0.11, 0.085, -0.654),
        (0.82, 0.53, -0.19, -0.066, 0.742),
        (0.09, -0.09, 0.77, -0.538, -0.151),
        (0.045, 0.41, -0.86, 0.476, 0.236),
    ]
    mass_changes = [(61, 3, 1.45), (104, 2, 0.72), (104, 4, 1.18), (137, 1, 0.91)]
    drag_zones = [(-0.65, 0.10, -0.30, 0.55, 0.85), (0.15, 0.72, -0.65, 0.15, 0.42)]
    burns = [(73, 3, 0.0035, -0.0024), (126, 1, -0.0018, 0.0029)]
    case_path = tmp_path / "damped.case"
    write_case(
        case_path,
        nsteps=166,
        dt=0.0011,
        output_every=19,
        g=0.88,
        substeps=5,
        recenter_every=31,
        bodies=bodies,
        drag_zones=drag_zones,
        mass_changes=mass_changes,
        burns=burns,
    )
    direct = tmp_path / "direct"
    first = tmp_path / "first"
    second = tmp_path / "second"
    resumed = tmp_path / "resumed"

    run_tool(case_path, direct)
    run_tool(case_path, first, "--checkpoint-at", "89")
    step, time, checkpoint_bodies = read_checkpoint(first / "checkpoint.dat")
    mutated = list(checkpoint_bodies)
    m2, x2, y2, vx2, vy2 = mutated[2]
    mutated[2] = (m2 * 1.06, x2 - 0.002, y2 + 0.003, vx2 + 0.012, vy2 - 0.009)
    write_checkpoint(first / "checkpoint.dat", step, time, mutated)
    run_tool(case_path, second, "--resume", str(first / "checkpoint.dat"), "--checkpoint-at", "128")
    run_tool(case_path, resumed, "--resume", str(second / "checkpoint.dat"))

    _, ref_inv, ref_final = reference_trajectory(
        start_step=step,
        start_time=time,
        nsteps=166,
        dt=0.0011,
        output_every=19,
        g=0.88,
        substeps=5,
        recenter_every=31,
        bodies=mutated,
        drag_zones=drag_zones,
        mass_changes=mass_changes,
        burns=burns,
    )
    assert max_state_delta(read_final(resumed / "final_state.tsv"), ref_final) < 3.0e-10
    observed_inv = read_invariants(resumed / "invariants.tsv")[166]
    assert max(abs(a - b) for a, b in zip(observed_inv, ref_inv[166])) < 3.0e-10
    assert max_state_delta(read_final(direct / "final_state.tsv"), read_final(resumed / "final_state.tsv")) > 1.0e-4


def test_drag_and_merges_use_microstep_order_before_checkpoint(tmp_path: Path) -> None:
    """Drag damping, coalescence, recentering, and checkpoint writes must share one absolute timeline."""
    bodies = [
        (0.63, -0.18, 0.03, 0.34, 0.012),
        (0.58, 0.19, -0.02, -0.31, -0.018),
        (0.11, -0.72, 0.42, 0.19, -0.23),
        (0.08, 0.66, -0.39, -0.21, 0.18),
    ]
    drag_zones = [(-0.35, 0.35, -0.22, 0.22, 1.75)]
    mass_changes = [(18, 1, 1.30), (18, 2, 0.80), (39, 3, 1.55)]
    case_path = tmp_path / "merge_drag.case"
    write_case(
        case_path,
        nsteps=74,
        dt=0.002,
        output_every=11,
        g=0.15,
        substeps=4,
        recenter_every=7,
        merge_radius=0.46,
        bodies=bodies,
        drag_zones=drag_zones,
        mass_changes=mass_changes,
    )
    direct = tmp_path / "direct"
    part = tmp_path / "part"
    resumed = tmp_path / "resumed"
    run_tool(case_path, direct)
    run_tool(case_path, part, "--checkpoint-at", "44")
    step, _time, nbody = read_checkpoint_header(part / "checkpoint.dat")
    assert (step, nbody) == (44, 3)
    run_tool(case_path, resumed, "--resume", str(part / "checkpoint.dat"))

    ref_traj, ref_inv, ref_final = reference_trajectory(
        start_step=0,
        start_time=0.0,
        nsteps=74,
        dt=0.002,
        output_every=11,
        g=0.15,
        substeps=4,
        recenter_every=7,
        merge_radius=0.46,
        bodies=bodies,
        drag_zones=drag_zones,
        mass_changes=mass_changes,
    )
    assert max_state_delta(read_final(direct / "final_state.tsv"), ref_final) < 3.0e-10
    assert max_state_delta(read_final(resumed / "final_state.tsv"), ref_final) < 3.0e-10
    trajectory = read_trajectory(direct / "trajectory.tsv")
    assert len(trajectory[44]) == len(ref_traj[44]) == 3
    assert max_state_delta({body: row for body, *row in trajectory[44]}, {body: row for body, *row in ref_traj[44]}) < 3.0e-10
    assert max(abs(a - b) for a, b in zip(read_invariants(direct / "invariants.tsv")[66], ref_inv[66])) < 3.0e-10
