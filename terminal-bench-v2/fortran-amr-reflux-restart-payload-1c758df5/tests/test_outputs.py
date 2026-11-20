import csv
import itertools
import math
import pathlib
import subprocess

ROOT = pathlib.Path('/app/amr')


def write_config(path, **overrides):
    values = {
        'nx': 24,
        'steps': 16,
        'cfl': 0.56,
        'velocity': 1.0,
        'refine_threshold': 0.018,
        'coarsen_threshold': 0.006,
        'checkpoint_interval': 4,
        'restart': 'false',
        'phase': 0.137,
    }
    values.update(overrides)
    path.write_text('\n'.join(f'{k}={v}' for k, v in values.items()) + '\n')


def run_case(tmp_path, name, checkpoint=None, **values):
    cfg = tmp_path / f'{name}.cfg'
    out = tmp_path / f'{name}.csv'
    chk = checkpoint or tmp_path / f'{name}.chk'
    write_config(cfg, **values)
    subprocess.run([str(ROOT / 'amr_solver'), str(cfg), str(out), str(chk)],
                   cwd=ROOT, check=True, timeout=30)
    return out, chk


def rows(path):
    with path.open(newline='') as handle:
        return [{k: float(v) if k != 'level' and k != 'index' else int(v)
                 for k, v in row.items()} for row in csv.DictReader(handle)]


def mass(cells):
    return sum((r['xhi'] - r['xlo']) * r['u'] for r in cells)


def complete_steps(path):
    result = []
    for line in path.read_text(errors='ignore').splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == 'END':
            result.append(int(fields[1]))
    return result


def assert_checkpoint_contract(path, nx, expected_steps=None):
    """Complete records have the documented schema, order, and base-cell rows."""
    records = checkpoint_records(path)
    assert records
    steps = []
    for block in records:
        begin = block[0].split()
        end = block[-1].split()
        assert len(begin) == 6 and begin[0] == 'BEGIN'
        assert len(end) == 3 and end[0] == 'END'
        step = int(begin[1])
        assert int(begin[2]) == nx
        assert int(end[1]) == step
        assert begin[5] == end[2]
        rows = block[1:-1]
        assert len(rows) == nx
        for j, line in enumerate(rows, start=1):
            fields = line.split()
            assert len(fields) == 6 and fields[0] == 'D'
            assert int(fields[1]) == j
            assert fields[2] in {'0', '1'}
        steps.append(step)
    assert steps == sorted(set(steps))
    if expected_steps is not None:
        assert steps == expected_steps


def checkpoint_records(path):
    blocks = []
    current = []
    for line in path.read_text(errors='ignore').splitlines():
        if line.startswith('BEGIN '):
            current = [line]
        elif current:
            current.append(line)
            if line.startswith('END '):
                blocks.append(current)
                current = []
    return blocks


def assert_hierarchy(cells):
    assert cells
    assert abs(cells[0]['xlo']) < 2e-15
    assert abs(cells[-1]['xhi'] - 1.0) < 2e-14
    for number, cell in enumerate(cells):
        assert cell['index'] == number
        assert cell['level'] in (0, 1)
        assert cell['xhi'] > cell['xlo']
    for left, right in itertools.pairwise(cells):
        assert abs(left['xhi'] - right['xlo']) < 2e-14
        assert max(left['xhi'] - left['xlo'], right['xhi'] - right['xlo']) <= 2.01 * min(left['xhi'] - left['xlo'], right['xhi'] - right['xlo'])


def exact_value(x, shift, phase):
    return 1 + 0.2 * math.sin(2 * math.pi * (x - shift - phase)) + 0.1 * math.sin(4 * math.pi * (x - shift - phase))


def exact_average(xlo, xhi, phase):
    width = xhi - xlo
    return (1
            + 0.2 * (math.cos(2 * math.pi * (xlo - phase)) - math.cos(2 * math.pi * (xhi - phase))) / (2 * math.pi * width)
            + 0.1 * (math.cos(4 * math.pi * (xlo - phase)) - math.cos(4 * math.pi * (xhi - phase))) / (4 * math.pi * width))


def test_zero_step_profile_uses_cell_averages(tmp_path):
    """The built-in periodic profile is emitted as finite-volume cell averages."""
    phase = 0.287
    out, _ = run_case(tmp_path, 'profile', nx=64, steps=0, cfl=0.37,
                      refine_threshold=0.02, coarsen_threshold=0.006, phase=phase)
    cells = rows(out)
    assert len(cells) == 64
    assert_hierarchy(cells)
    assert all(r['level'] == 0 for r in cells)
    for cell in cells:
        expected = exact_average(cell['xlo'], cell['xhi'], phase)
        assert abs(cell['u'] - expected) < 2e-13


def test_conservative_adaptive_hierarchy(tmp_path):
    """Adaptive leaves form a conservative periodic finite-volume hierarchy."""
    out, _ = run_case(tmp_path, 'adaptive', nx=32, steps=35, cfl=0.61,
                      refine_threshold=0.011, coarsen_threshold=0.004, phase=0.231)
    cells = rows(out)
    assert_hierarchy(cells)
    assert any(r['level'] == 1 for r in cells)
    assert abs(mass(cells) - 1.0) < 3e-11


def test_stable_transport_in_both_directions(tmp_path):
    """The conservative update remains bounded and transports either velocity sign."""
    for j, velocity in enumerate((0.8, -1.25)):
        out, _ = run_case(tmp_path, f'direction{j}', nx=48, steps=28, cfl=0.42,
                          velocity=velocity, refine_threshold=0.009,
                          coarsen_threshold=0.003, phase=0.071 + 0.11 * j)
        cells = rows(out)
        assert_hierarchy(cells)
        assert abs(mass(cells) - 1.0) < 3e-11
        assert min(r['u'] for r in cells) > 0.62
        assert max(r['u'] for r in cells) < 1.38


def test_restart_matches_uninterrupted_topology(tmp_path):
    """A resumed adaptive trajectory reproduces uninterrupted leaf values and topology."""
    checkpoint = tmp_path / 'shared.chk'
    run_case(tmp_path, 'prefix', checkpoint, nx=28, steps=11, cfl=0.53,
             checkpoint_interval=3, phase=0.319)
    resumed, _ = run_case(tmp_path, 'resumed', checkpoint, nx=28, steps=27, cfl=0.53,
                          checkpoint_interval=3, restart='true', phase=0.319)
    direct, _ = run_case(tmp_path, 'direct', nx=28, steps=27, cfl=0.53,
                         checkpoint_interval=3, phase=0.319)
    assert resumed.read_bytes() == direct.read_bytes()
    saved_steps = complete_steps(checkpoint)
    assert saved_steps == sorted(set(saved_steps))
    assert_checkpoint_contract(checkpoint, 28)


def test_restart_prunes_future_checkpoint_history(tmp_path):
    """Branching from an earlier target removes future records before appending."""
    checkpoint = tmp_path / 'future.chk'
    args = {'nx': 36, 'cfl': 0.51, 'velocity': 0.95,
            'refine_threshold': 0.010, 'coarsen_threshold': 0.0035, 'phase': 0.266}
    run_case(tmp_path, 'long', checkpoint, **{**args, 'steps': 18, 'checkpoint_interval': 6})
    resumed, _ = run_case(tmp_path, 'branch', checkpoint,
                          **{**args, 'steps': 10, 'checkpoint_interval': 5, 'restart': 'true'})
    direct, _ = run_case(tmp_path, 'direct_branch', **{**args, 'steps': 10, 'checkpoint_interval': 5})
    assert resumed.read_bytes() == direct.read_bytes()
    assert_checkpoint_contract(checkpoint, 36, [6, 10])


def test_restart_rejects_same_size_incompatible_config(tmp_path):
    """Same-size checkpoint records from different evolution settings are cleared."""
    checkpoint = tmp_path / 'incompatible.chk'
    run_case(tmp_path, 'old_physics', checkpoint, nx=32, steps=8, cfl=0.44,
             velocity=1.10, checkpoint_interval=4, phase=0.117,
             refine_threshold=0.014, coarsen_threshold=0.004)
    resumed, _ = run_case(tmp_path, 'new_physics', checkpoint, nx=32, steps=6, cfl=0.44,
                          velocity=1.10, checkpoint_interval=3, restart='true', phase=0.377,
                          refine_threshold=0.014, coarsen_threshold=0.004)
    direct, _ = run_case(tmp_path, 'direct_new_physics', nx=32, steps=6, cfl=0.44,
                         velocity=1.10, checkpoint_interval=3, phase=0.377,
                         refine_threshold=0.014, coarsen_threshold=0.004)
    assert resumed.read_bytes() == direct.read_bytes()
    assert_checkpoint_contract(checkpoint, 32, [3, 6])


def test_branch_restart_prunes_mixed_history_and_preserves_contract(tmp_path):
    """Compatible history survives while records from a later incompatible branch do not."""
    checkpoint = tmp_path / 'mixed_branch.chk'
    common = {'nx': 40, 'cfl': 0.46, 'velocity': -0.85,
              'refine_threshold': 0.011, 'coarsen_threshold': 0.003, 'checkpoint_interval': 4}
    run_case(tmp_path, 'branch_a_prefix', checkpoint, **{**common, 'steps': 8, 'phase': 0.144})
    run_case(tmp_path, 'branch_b', checkpoint, **{**common, 'steps': 6, 'checkpoint_interval': 3, 'phase': 0.344})
    resumed, _ = run_case(tmp_path, 'resume_a', checkpoint,
                          **{**common, 'steps': 12, 'restart': 'true', 'phase': 0.144})
    direct, _ = run_case(tmp_path, 'direct_a', **{**common, 'steps': 12, 'phase': 0.144})
    assert resumed.read_bytes() == direct.read_bytes()
    assert_checkpoint_contract(checkpoint, 40, [4, 8, 12])


def test_incomplete_and_invalid_tail_recovery(tmp_path):
    """Recovery selects the last complete compatible state despite damaged trailing data."""
    checkpoint = tmp_path / 'damaged.chk'
    run_case(tmp_path, 'saved', checkpoint, nx=20, steps=12, cfl=0.47,
             checkpoint_interval=4, phase=0.413)
    with checkpoint.open('ab') as handle:
        handle.write(b'BEGIN 999 20 1.0 7\nD 1 1 1.0')
    resumed, _ = run_case(tmp_path, 'recovered', checkpoint, nx=20, steps=21, cfl=0.47,
                          checkpoint_interval=4, restart='true', phase=0.413)
    direct, _ = run_case(tmp_path, 'expected', nx=20, steps=21, cfl=0.47,
                         checkpoint_interval=4, phase=0.413)
    assert resumed.read_bytes() == direct.read_bytes()


def test_restart_skips_invalid_complete_records(tmp_path):
    """Restart scanning ignores a bad complete record and uses a later valid one."""
    checkpoint = tmp_path / 'mixed.chk'
    args = {'nx': 24, 'steps': 16, 'cfl': 0.49, 'velocity': -0.9,
            'refine_threshold': 0.012, 'coarsen_threshold': 0.004,
            'checkpoint_interval': 4, 'phase': 0.193}
    run_case(tmp_path, 'seed', checkpoint, **args)
    blocks = checkpoint_records(checkpoint)
    assert len(blocks) >= 4
    bad = list(blocks[2])
    d_rows = [i for i, line in enumerate(bad) if line.startswith('D ')]
    parts = bad[d_rows[-1]].split()
    parts[1] = str(int(parts[1]) + 1)
    bad[d_rows[-1]] = ' '.join(parts)
    rebuilt = blocks[0] + blocks[1] + bad + blocks[3]
    checkpoint.write_text('\n'.join(rebuilt) + '\n')
    resumed, _ = run_case(tmp_path, 'resume_mixed', checkpoint, **{**args, 'steps': 20, 'restart': 'true'})
    direct, _ = run_case(tmp_path, 'direct_mixed', **{**args, 'steps': 20})
    assert resumed.read_bytes() == direct.read_bytes()
    saved = complete_steps(checkpoint)
    assert saved == sorted(set(saved))
    assert saved[-1] == 20
    assert_checkpoint_contract(checkpoint, 24)


def test_restart_skips_malformed_middle_record_and_uses_later_valid(tmp_path):
    """A malformed complete record in the middle cannot shadow a later valid restart."""
    checkpoint = tmp_path / 'middle_malformed.chk'
    args = {'nx': 30, 'steps': 16, 'cfl': 0.43, 'velocity': 1.15,
            'refine_threshold': 0.0105, 'coarsen_threshold': 0.0035,
            'checkpoint_interval': 4, 'phase': 0.257}
    run_case(tmp_path, 'seed_middle_malformed', checkpoint, **args)
    blocks = checkpoint_records(checkpoint)
    assert complete_steps(checkpoint) == [4, 8, 12, 16]

    bad = list(blocks[1])
    row = next(i for i, line in enumerate(bad) if line.startswith('D 5 '))
    fields = bad[row].split()
    fields[3] = 'not-a-real'
    bad[row] = ' '.join(fields)
    checkpoint.write_text('\n'.join(blocks[0] + bad + blocks[2] + blocks[3]) + '\n')

    resumed, _ = run_case(tmp_path, 'resume_middle_malformed', checkpoint,
                          **{**args, 'steps': 20, 'restart': 'true'})
    direct, _ = run_case(tmp_path, 'direct_middle_malformed', **{**args, 'steps': 20})
    assert resumed.read_bytes() == direct.read_bytes()
    assert_checkpoint_contract(checkpoint, 30, [4, 12, 16, 20])


def test_restart_ignores_out_of_record_noise_and_preserves_later_state(tmp_path):
    """Bytes outside complete records are not checkpoint history and cannot mask later states."""
    checkpoint = tmp_path / 'noisy_history.chk'
    args = {'nx': 38, 'steps': 20, 'cfl': 0.41, 'velocity': -1.05,
            'refine_threshold': 0.010, 'coarsen_threshold': 0.0032,
            'checkpoint_interval': 5, 'phase': 0.221}
    run_case(tmp_path, 'seed_noisy_history', checkpoint, **args)
    blocks = checkpoint_records(checkpoint)
    assert complete_steps(checkpoint) == [5, 10, 15, 20]

    noisy = [
        'operator note: retry left a partial header here',
        'D 1 1 0.0 0.0 0.0',
        'not an END record',
    ]
    checkpoint.write_text('\n'.join(blocks[0] + noisy + blocks[1] + blocks[2] + blocks[3]) + '\n')
    resumed, _ = run_case(tmp_path, 'resume_noisy_history', checkpoint,
                          **{**args, 'steps': 25, 'restart': 'true'})
    direct, _ = run_case(tmp_path, 'direct_noisy_history', **{**args, 'steps': 25})
    assert resumed.read_bytes() == direct.read_bytes()
    assert_checkpoint_contract(checkpoint, 38, [5, 10, 15, 20, 25])


def test_restart_config_checksum_covers_transport_and_adaptation_settings(tmp_path):
    """Compatible checkpoint branches distinguish all documented evolution settings."""
    checkpoint = tmp_path / 'config_signature.chk'
    common = {'nx': 44, 'velocity': 0.92, 'phase': 0.181,
              'checkpoint_interval': 4}
    original = {**common, 'steps': 12, 'cfl': 0.40,
                'refine_threshold': 0.010, 'coarsen_threshold': 0.0030}
    shifted = {**common, 'steps': 8, 'cfl': 0.48,
               'refine_threshold': 0.013, 'coarsen_threshold': 0.0042}
    run_case(tmp_path, 'signature_original', checkpoint, **original)
    resumed, _ = run_case(tmp_path, 'signature_shifted', checkpoint,
                          **{**shifted, 'restart': 'true'})
    direct, _ = run_case(tmp_path, 'signature_direct', **shifted)
    assert resumed.read_bytes() == direct.read_bytes()
    assert_checkpoint_contract(checkpoint, 44, [4, 8])


def test_restart_drops_future_and_nonmonotone_records(tmp_path):
    """Branch selection ignores future records and later duplicate/nonmonotone records."""
    checkpoint = tmp_path / 'nonmonotone.chk'
    stray_checkpoint = tmp_path / 'stray_step10.chk'
    args = {'nx': 34, 'cfl': 0.58, 'velocity': -0.72,
            'refine_threshold': 0.012, 'coarsen_threshold': 0.004,
            'phase': 0.189}
    run_case(tmp_path, 'seed_nonmonotone', checkpoint,
             **{**args, 'steps': 16, 'checkpoint_interval': 4})
    run_case(tmp_path, 'stray_step10', stray_checkpoint,
             **{**args, 'steps': 10, 'checkpoint_interval': 10})
    blocks = checkpoint_records(checkpoint)
    stray = checkpoint_records(stray_checkpoint)[0]
    assert complete_steps(checkpoint) == [4, 8, 12, 16]
    assert complete_steps(stray_checkpoint) == [10]

    checkpoint.write_text('\n'.join(line for block in blocks + [stray] for line in block) + '\n')
    resumed, _ = run_case(tmp_path, 'resume_nonmonotone', checkpoint,
                          **{**args, 'steps': 14, 'checkpoint_interval': 7, 'restart': 'true'})
    direct, _ = run_case(tmp_path, 'direct_nonmonotone',
                         **{**args, 'steps': 14, 'checkpoint_interval': 7})
    assert resumed.read_bytes() == direct.read_bytes()
    assert_checkpoint_contract(checkpoint, 34, [4, 8, 12, 14])


def test_repeated_segmented_restarts_preserve_branch_history(tmp_path):
    """Multiple restart segments must remain equivalent to one uninterrupted run."""
    checkpoint = tmp_path / 'segmented.chk'
    args = {'nx': 52, 'cfl': 0.39, 'velocity': 0.83,
            'refine_threshold': 0.0095, 'coarsen_threshold': 0.0028,
            'phase': 0.329}
    run_case(tmp_path, 'segment_a', checkpoint,
             **{**args, 'steps': 7, 'checkpoint_interval': 7})
    mid, _ = run_case(tmp_path, 'segment_b', checkpoint,
                      **{**args, 'steps': 11, 'checkpoint_interval': 5, 'restart': 'true'})
    direct_mid, _ = run_case(tmp_path, 'direct_segment_mid',
                             **{**args, 'steps': 11, 'checkpoint_interval': 5})
    assert mid.read_bytes() == direct_mid.read_bytes()

    final, _ = run_case(tmp_path, 'segment_c', checkpoint,
                        **{**args, 'steps': 23, 'checkpoint_interval': 6, 'restart': 'true'})
    direct_final, _ = run_case(tmp_path, 'direct_segment_final',
                               **{**args, 'steps': 23, 'checkpoint_interval': 6})
    assert final.read_bytes() == direct_final.read_bytes()
    assert_checkpoint_contract(checkpoint, 52, [7, 10, 11, 12, 18, 23])


def test_generated_cases_are_deterministic(tmp_path):
    """Generated legal configurations give repeatable scientific output."""
    for j, nx in enumerate((17, 31, 43)):
        args = {
            'nx': nx,
            'steps': 13 + j,
            'cfl': 0.35 + 0.08 * j,
            'velocity': (-1) ** j * (0.7 + 0.2 * j),
            'refine_threshold': 0.012 + 0.002 * j,
            'coarsen_threshold': 0.003,
            'phase': 0.09 + 0.173 * j,
        }
        first, first_chk = run_case(tmp_path, f'gen{j}a', **args)
        second, second_chk = run_case(tmp_path, f'gen{j}b', **args)
        assert first.read_bytes() == second.read_bytes()
        assert first_chk.read_bytes() == second_chk.read_bytes()
