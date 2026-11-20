#!/bin/bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

path = Path('/app/dg_swe/swe_dg.f90')
src = path.read_text()
src = src.replace('''  checkpoint_interval = 0; restart = .false.
  call read_config(trim(cfg), cells, steps, cfl, gravity, slope, hump, dry_floor, checkpoint_interval, restart)
  allocate(h0(cells), h1(cells), q0(cells), q1(cells))''', '''  checkpoint_interval = 0; restart = .false.
  call read_config(trim(cfg), cells, steps, cfl, gravity, slope, hump, dry_floor, checkpoint_interval, restart)
  call validate_config(cells, steps, cfl, dry_floor, checkpoint_interval)
  allocate(h0(cells), h1(cells), q0(cells), q1(cells))''')
src = src.replace('''  if (restart) then
     call load_checkpoint(trim(chk), cells, steps, start_step, time, h0, h1, q0, q1)
     start_step = 0
     h1 = 0.0_dp
     q1 = 0.0_dp
  end if''', '''  if (restart) then
     call load_checkpoint(trim(chk), cells, steps, cfl, start_step, time, h0, h1, q0, q1)
  else if (checkpoint_interval > 0) then
     call remove_existing(trim(chk))
   end if''')
src = src.replace('''       if (h0(i) < 0.55_dp * dry_floor) h0(i) = 0.55_dp * dry_floor''', '''       if (h0(i) < dry_floor) h0(i) = dry_floor''')
src = src.replace('''    do i = 0, cells
       l = max(1, i); r = min(cells, i + 1)''', '''    do i = 0, cells
       if (i == 0) then
          l = cells; r = 1
       else if (i == cells) then
          l = cells; r = 1
       else
          l = i; r = i + 1
       end if''')
src = src.replace('''    do i = 1, cells
       h0(i) = max(dry_floor, nh(i))
       q0(i) = nq(i)
       h1(i) = limited_slope(h0(mod(i,cells)+1) - h0(i), h0(i) - h0(mod(i-2+cells,cells)+1))
       q1(i) = limited_slope(q0(mod(i,cells)+1) - q0(i), q0(i) - q0(mod(i-2+cells,cells)+1))
       if (h0(i) <= 1.05_dp*dry_floor) q0(i) = 0.0_dp
    end do
    mass = sum(h0) / real(cells, dp)''', '''    mass = sum(h0)
    do i = 1, cells
       h0(i) = max(dry_floor, nh(i))
       q0(i) = nq(i)
    end do
    call restore_mass(cells, dry_floor, mass, h0)
    do i = 1, cells
       h1(i) = limited_slope(h0(mod(i,cells)+1) - h0(i), h0(i) - h0(mod(i-2+cells,cells)+1))
       q1(i) = limited_slope(q0(mod(i,cells)+1) - q0(i), q0(i) - q0(mod(i-2+cells,cells)+1))
        if (h0(i) <= 1.05_dp*dry_floor) then
           h0(i) = dry_floor
           q0(i) = 0.0_dp
           q1(i) = 0.0_dp
        end if
    end do
    mass = sum(h0) / real(cells, dp)''')
src = src.replace('''  real(dp) function limited_slope(a, b)''', '''  subroutine restore_mass(cells, dry_floor, target, h0)
    integer, intent(in) :: cells
    real(dp), intent(in) :: dry_floor, target
    real(dp), intent(inout) :: h0(:)
    integer :: i, active, pass
    real(dp) :: extra, share
    do pass = 1, cells
       extra = sum(h0) - target
       if (abs(extra) <= 1.0e-14_dp * max(1.0_dp, abs(target))) exit
       active = count(h0 > dry_floor + 1.0e-15_dp)
       if (active <= 0) exit
       share = extra / real(active, dp)
       do i = 1, cells
          if (h0(i) > dry_floor + 1.0e-15_dp) h0(i) = max(dry_floor, h0(i) - share)
       end do
    end do
  end subroutine

  real(dp) function limited_slope(a, b)''')
src = src.replace('''  subroutine initialize(cells, hump, dry_floor, h0, h1, q0, q1)''', '''  subroutine validate_config(cells, steps, cfl, dry_floor, interval)
    integer, intent(in) :: cells, steps, interval
    real(dp), intent(in) :: cfl, dry_floor
    if (cells < 24 .or. cells > 96 .or. steps < 0 .or. cfl <= 0.0_dp .or. cfl > 0.24_dp .or. dry_floor < 0.0_dp .or. interval < 0) then
       error stop 'invalid config'
    end if
  end subroutine

  subroutine initialize(cells, hump, dry_floor, h0, h1, q0, q1)''')
src = src.replace('''  subroutine load_checkpoint(path, cells, target, best_step, best_time, h0, h1, q0, q1)
    character(len=*), intent(in) :: path
    integer, intent(in) :: cells, target
    integer, intent(out) :: best_step
    real(dp), intent(out) :: best_time, h0(:), h1(:), q0(:), q1(:)
    integer :: unit, ios, c, step, i, idx
    real(dp) :: t, want, got, tail, th0(cells), th1(cells), tq0(cells), tq1(cells)
    character(len=32) :: tag, magic
    best_step = 0; best_time = 0.0_dp
    open(newunit=unit, file=path, status='old', action='read', iostat=ios)
    if (ios /= 0) return
    do
       read(unit, *, iostat=ios) tag, magic, c, step, t, want
       if (ios /= 0) exit
       if (trim(tag) /= 'BEGIN' .or. trim(magic) /= 'DGSW1' .or. c /= cells) exit
       do i = 1, cells
          read(unit, *, iostat=ios) idx, th0(i), th1(i), tq0(i), tq1(i)
          if (ios /= 0 .or. idx /= i) exit
       end do
       if (ios /= 0) exit
       read(unit, *, iostat=ios) tag, tail
       if (ios /= 0 .or. trim(tag) /= 'END') exit
       got = checksum(cells, step, t, th0, th1, tq0, tq1)
       if (abs(got - want) < 1.0e-9_dp .and. abs(got - tail) < 1.0e-9_dp .and. step <= target) then
          best_step = step; best_time = t; h0 = th0; h1 = th1; q0 = tq0; q1 = tq1
       end if
    end do
    close(unit)
  end subroutine''', '''  subroutine load_checkpoint(path, cells, target, cfl, best_step, best_time, h0, h1, q0, q1)
    character(len=*), intent(in) :: path
    integer, intent(in) :: cells, target
    real(dp), intent(in) :: cfl
    integer, intent(out) :: best_step
    real(dp), intent(out) :: best_time, h0(:), h1(:), q0(:), q1(:)
     integer :: unit, ios, c, step, i, idx
     real(dp) :: t, want, got, tail, expected_time, th0(cells), th1(cells), tq0(cells), tq1(cells)
     character(len=512) :: line
     character(len=32) :: tag, magic
     logical :: valid
    best_step = 0; best_time = 0.0_dp
    open(newunit=unit, file=path, status='old', action='read', iostat=ios)
    if (ios /= 0) return
    do
       read(unit, '(A)', iostat=ios) line
       if (ios /= 0) exit
       if (index(adjustl(line), 'BEGIN DGSW1') /= 1) cycle
       read(line, *, iostat=ios) tag, magic, c, step, t, want
       if (ios /= 0 .or. trim(tag) /= 'BEGIN' .or. trim(magic) /= 'DGSW1') cycle
       if (c /= cells) then
          do i = 1, max(c, 0) + 1
             read(unit, '(A)', iostat=ios) line
             if (ios /= 0) exit
             if (index(adjustl(line), 'END ') == 1) exit
          end do
          if (ios /= 0) exit
          cycle
       end if
        valid = .true.
        do i = 1, cells
           read(unit, '(A)', iostat=ios) line
           if (ios /= 0) exit
           if (valid) then
              read(line, *, iostat=ios) idx, th0(i), th1(i), tq0(i), tq1(i)
              if (ios /= 0 .or. idx /= i) valid = .false.
           end if
        end do
        if (ios /= 0) exit
        read(unit, '(A)', iostat=ios) line
        if (ios /= 0) exit
        read(line, *, iostat=ios) tag, tail
        if (ios /= 0 .or. trim(tag) /= 'END') cycle
        if (valid) then
           got = checksum(cells, step, t, th0, th1, tq0, tq1)
            expected_time = real(step, dp) * cfl / real(cells, dp)
            if (abs(got - want) < 1.0e-9_dp .and. abs(got - tail) < 1.0e-9_dp .and. abs(t - expected_time) <= 1.0e-12_dp .and. step <= target) then
               best_step = step; best_time = t; h0 = th0; h1 = th1; q0 = tq0; q1 = tq1
            end if
        end if
    end do
    close(unit)
  end subroutine''')
src = src.replace('''  subroutine write_csv(path, cells, h0, h1, q0, q1)''', '''  subroutine remove_existing(path)
    character(len=*), intent(in) :: path
    integer :: unit, ios
    open(newunit=unit, file=path, status='old', action='readwrite', iostat=ios)
    if (ios == 0) close(unit, status='delete')
  end subroutine

  subroutine write_csv(path, cells, h0, h1, q0, q1)''')
path.write_text(src)
PY

make -C /app/dg_swe clean
make -C /app/dg_swe
