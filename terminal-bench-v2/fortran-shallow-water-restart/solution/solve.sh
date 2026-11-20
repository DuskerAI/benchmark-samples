#!/bin/bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

path = Path('/opt/swe/src/shallow_water.f90')
text = path.read_text()

replacements = {
    "write(unit, '(3(I0,1X),ES26.17,1X,ES26.17,1X,ES26.17,1X,ES26.17)') &\n      nx, step - 1, seed, phase, pulse - 0.001_real64, spin * 0.5_real64, drift - 0.002_real64":
        "write(unit, '(3(I0,1X),ES26.17,1X,ES26.17,1X,ES26.17,1X,ES26.17)') &\n      nx, step, seed, phase, pulse, spin, drift",
    "write(unit, '(I0,1X,ES26.17,1X,ES26.17,1X,ES14.6,1X,ES14.6,1X,ES14.6,1X,ES14.6,1X,ES14.6,1X,ES14.6)') &\n        i, h(i), u(shifted), real(memory(i), kind=real32), real(lag(previous), kind=real32), &\n        real(terrain(shifted), kind=real32), real(stress(previous), kind=real32), &\n        real(tracer(previous), kind=real32), real(swirl(shifted), kind=real32)":
        "write(unit, '(I0,1X,ES26.17,1X,ES26.17,1X,ES26.17,1X,ES26.17,1X,ES26.17,1X,ES26.17,1X,ES26.17,1X,ES26.17)') &\n        i, h(i), u(i), memory(i), lag(i), terrain(i), stress(i), tracer(i), swirl(i)",
    "    seed = seed + 17\n": "",
    "    phase = 0.0_real64\n": "    phase = phase_file\n",
    "    pulse = 0.0_real64\n": "    pulse = pulse_file\n",
    "    spin = 0.0_real64\n": "    spin = spin_file\n",
    "    drift = 0.0_real64\n": "    drift = drift_file\n",
    "      tracer(i) = 0.0_real64\n": "",
    "      swirl(i) = 0.0_real64\n": "",
}

for before, after in replacements.items():
    if before not in text:
        raise SystemExit(f'missing expected source fragment: {before!r}')
    text = text.replace(before, after)

start = text.index("  subroutine parse_split_list(spec, steps, splits, nsplits)\n")
end = text.index("  end subroutine parse_split_list\n", start) + len("  end subroutine parse_split_list\n")
text = text[:start] + """  subroutine parse_split_list(spec, steps, splits, nsplits)
    character(len=*), intent(in) :: spec
    integer, intent(in) :: steps
    integer, allocatable, intent(out) :: splits(:)
    integer, intent(out) :: nsplits
    integer :: i, comma, start_pos, value, previous
    character(len=256) :: token

    nsplits = 1
    do i = 1, len_trim(spec)
      if (spec(i:i) == ',') nsplits = nsplits + 1
    end do
    allocate(splits(nsplits))
    start_pos = 1
    previous = -1
    do i = 1, nsplits
      comma = index(spec(start_pos:), ',')
      if (comma == 0) then
        token = spec(start_pos:)
      else
        token = spec(start_pos:start_pos + comma - 2)
      end if
      if (len_trim(token) == 0) error stop 'empty split entry'
      read(token, *) value
      if (value < 0 .or. value > steps) error stop 'split must be in [0, steps]'
      if (value < previous) error stop 'split list must be ordered'
      splits(i) = value
      previous = value
      if (comma > 0) start_pos = start_pos + comma
    end do
  end subroutine parse_split_list
""" + text[end:]

needle = "  subroutine write_checkpoint(path, nx, step, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)\n"
insert = """  subroutine ensure_parent(path)
    character(len=*), intent(in) :: path
    integer :: j
    do j = len_trim(path), 1, -1
      if (path(j:j) == '/') then
        if (j > 1) call execute_command_line('mkdir -p "' // path(:j-1) // '"')
        exit
      end if
    end do
  end subroutine ensure_parent

  subroutine append_audit(path, segment, step, nx, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    character(len=*), intent(in) :: path
    integer, intent(in) :: segment, step, nx
    real(real64), intent(in) :: h(nx), u(nx), memory(nx), lag(nx), terrain(nx), stress(nx)
    real(real64), intent(in) :: tracer(nx), swirl(nx), phase, pulse, spin, drift
    integer :: unit
    real(real64) :: mass, momentum, energy

    mass = sum(h)
    momentum = sum(h * u)
    energy = sum(0.5_real64 * h * u * u + 0.5_real64 * h * h)
    call ensure_parent(path)
    if (segment == 1) then
      open(newunit=unit, file=path, status='replace', action='write')
      write(unit, '(A)') 'segment,step,mass,momentum,energy,phase,pulse,spin,drift,' // &
        'memory_l2,lag_l2,terrain_l2,stress_l2,tracer_l2,swirl_l2'
    else
      open(newunit=unit, file=path, status='old', position='append', action='write')
    end if
    write(unit, '(I0,A,I0,13(A,ES26.17))') segment, ',', step, ',', mass, ',', momentum, ',', energy, ',', &
      phase, ',', pulse, ',', spin, ',', drift, ',', sqrt(sum(memory * memory)), ',', sqrt(sum(lag * lag)), ',', &
      sqrt(sum(terrain * terrain)), ',', sqrt(sum(stress * stress)), ',', sqrt(sum(tracer * tracer)), ',', sqrt(sum(swirl * swirl))
    close(unit)
  end subroutine append_audit

  subroutine write_checkpoint(path, nx, step, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
"""
if needle not in text:
    raise SystemExit('missing write_checkpoint declaration')
text = text.replace(needle, insert)

audit_needle = "      call read_checkpoint(trim(checkpoint), nx, loaded_step, loaded_seed, h, u, memory, &\n                           lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)\n      current_step = loaded_step\n"
audit_insert = "      call read_checkpoint(trim(checkpoint), nx, loaded_step, loaded_seed, h, u, memory, &\n                           lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)\n      call append_audit(trim(prefix) // '_restart_audit.csv', segment, loaded_step, nx, h, u, memory, &\n                        lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)\n      current_step = loaded_step\n"
if audit_needle not in text:
    raise SystemExit('missing pair reload block')
text = text.replace(audit_needle, audit_insert)

resume_guard_needle = "    call read_checkpoint(trim(checkpoint), nx, loaded_step, loaded_seed, h, u, memory, &\n                         lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)\n    do step = loaded_step + 1, steps\n"
resume_guard_insert = "    call read_checkpoint(trim(checkpoint), nx, loaded_step, loaded_seed, h, u, memory, &\n                         lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)\n    if (loaded_step > steps) error stop 'restart step exceeds requested steps'\n    do step = loaded_step + 1, steps\n"
if resume_guard_needle not in text:
    raise SystemExit('missing resume loaded-step block')
text = text.replace(resume_guard_needle, resume_guard_insert)

read_decl_needle = "    integer :: unit, nx_file, i, idx\n    real(real64) :: phase_file, pulse_file, spin_file, drift_file\n"
read_decl_insert = "    integer :: unit, nx_file, i, idx, ios\n    character(len=256) :: extra_line\n    real(real64) :: phase_file, pulse_file, spin_file, drift_file\n"
if read_decl_needle not in text:
    raise SystemExit('missing read-checkpoint declaration block')
text = text.replace(read_decl_needle, read_decl_insert)

row_count_needle = "    end do\n    close(unit)\n  end subroutine read_checkpoint\n"
row_count_insert = "    end do\n    read(unit, '(A)', iostat=ios) extra_line\n    if (ios == 0 .and. len_trim(extra_line) > 0) error stop 'restart row count is invalid'\n    close(unit)\n  end subroutine read_checkpoint\n"
if row_count_needle not in text:
    raise SystemExit('missing read-checkpoint close block')
text = text.replace(row_count_needle, row_count_insert)

open_line = "    open(newunit=unit, file=path, status='replace', action='write')\n"
if open_line not in text:
    raise SystemExit('missing replace-open line')
text = text.replace(open_line, "    call ensure_parent(path)\n" + open_line)

path.write_text(text)
PY

make -C /opt/swe clean all
