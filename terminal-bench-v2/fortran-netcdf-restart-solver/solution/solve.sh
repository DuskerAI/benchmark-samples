#!/bin/bash
set -euo pipefail

cd /app/task_file

cat > src/model.f90 <<'F90'
module model_mod
  use iso_fortran_env, only: real64
  use config_mod, only: run_config
  implicit none

contains

  subroutine initialize_state(cfg, u)
    type(run_config), intent(in) :: cfg
    real(real64), intent(out) :: u(:)
    integer :: i, n
    real(real64) :: x, pi
    n = size(u)
    pi = acos(-1.0_real64)
    do i = 1, n
      x = real(i - 1, real64) / real(n - 1, real64)
      u(i) = cfg%left_bc * (1.0_real64 - x) + cfg%right_bc * x &
        + sin(pi * x) + 0.25_real64 * sin(3.0_real64 * pi * x)
    end do
    u(1) = cfg%left_bc
    u(n) = cfg%right_bc
  end subroutine initialize_state

  subroutine enforce_boundaries(cfg, u)
    type(run_config), intent(in) :: cfg
    real(real64), intent(inout) :: u(:)
    u(1) = cfg%left_bc
    u(size(u)) = cfg%right_bc
  end subroutine enforce_boundaries

  subroutine advance_state(cfg, u)
    type(run_config), intent(in) :: cfg
    real(real64), intent(inout) :: u(:)
    real(real64), allocatable :: next(:)
    real(real64) :: r
    integer :: i, n
    n = size(u)
    call enforce_boundaries(cfg, u)
    allocate(next(n))
    r = cfg%diffusion * cfg%dt * real((n - 1) * (n - 1), real64)
    next(1) = cfg%left_bc
    next(n) = cfg%right_bc
    do i = 2, n - 1
      next(i) = u(i) + r * (u(i - 1) - 2.0_real64 * u(i) + u(i + 1))
    end do
    u = next
    deallocate(next)
  end subroutine advance_state

  subroutine write_output(path, cfg, u)
    character(len=*), intent(in) :: path
    type(run_config), intent(in) :: cfg
    real(real64), intent(inout) :: u(:)
    integer :: unit, i, n
    real(real64) :: x
    n = size(u)
    call enforce_boundaries(cfg, u)
    open(newunit=unit, file=path, status='replace', action='write')
    write(unit,'(a)') 'index,x,u'
    do i = 1, n
      x = real(i - 1, real64) / real(n - 1, real64)
      write(unit,'(i0,a,es25.17,a,es25.17)') i, ',', x, ',', u(i)
    end do
    close(unit)
  end subroutine write_output

end module model_mod
F90

cat > src/checkpoint.f90 <<'F90'
module checkpoint_mod
  use iso_fortran_env, only: int32, int64, real64
  use config_mod, only: run_config
  implicit none

contains

  subroutine last_checkpoint_step(path, nx_expected, last_step)
    character(len=*), intent(in) :: path
    integer, intent(in) :: nx_expected
    integer, intent(out) :: last_step
    integer :: unit, ios
    integer(int64) :: pos, fsize, endpos, expected
    integer(int32) :: nbytes, step32, nx32
    real(real64) :: t
    character(len=4) :: magic
    logical :: exists

    last_step = -1
    inquire(file=path, exist=exists, size=fsize)
    if (.not. exists .or. fsize < 8_int64) return
    open(newunit=unit, file=path, access='stream', form='unformatted', &
      status='old', action='read', iostat=ios)
    if (ios /= 0) return
    pos = 1_int64
    do while (pos + 7_int64 <= fsize)
      read(unit, pos=pos, iostat=ios) magic
      if (ios /= 0 .or. magic /= 'FNC1') exit
      read(unit, pos=pos + 4_int64, iostat=ios) nbytes
      if (ios /= 0 .or. nbytes < 16) exit
      endpos = pos + 8_int64 + int(nbytes, int64) - 1_int64
      if (endpos > fsize) exit
      read(unit, pos=pos + 8_int64, iostat=ios) step32, nx32, t
      if (ios /= 0) exit
      expected = 16_int64 + 8_int64 * int(nx32, int64)
      if (nx32 == nx_expected .and. int(nbytes, int64) == expected .and. step32 >= 0) then
        if (int(step32) > last_step) last_step = int(step32)
      end if
      pos = endpos + 1_int64
    end do
    close(unit)
  end subroutine last_checkpoint_step

  subroutine append_checkpoint(path, step, t, u)
    character(len=*), intent(in) :: path
    integer, intent(in) :: step
    real(real64), intent(in) :: t
    real(real64), intent(in) :: u(:)
    integer :: unit, last_step
    character(len=4) :: magic
    integer(int32) :: nbytes, step32, nx32
    if (len_trim(path) == 0) return
    call last_checkpoint_step(path, size(u), last_step)
    if (step <= last_step) return
    magic = 'FNC1'
    step32 = int(step, int32)
    nx32 = int(size(u), int32)
    nbytes = int(16 + 8 * size(u), int32)
    open(newunit=unit, file=path, access='stream', form='unformatted', &
      status='unknown', position='append', action='write')
    write(unit) magic
    write(unit) nbytes
    write(unit) step32
    write(unit) nx32
    write(unit) t
    write(unit) u
    close(unit)
  end subroutine append_checkpoint

  subroutine load_checkpoint(path, target_steps, cfg, u, start_step, ok)
    character(len=*), intent(in) :: path
    integer, intent(in) :: target_steps
    type(run_config), intent(in) :: cfg
    real(real64), intent(out) :: u(:)
    integer, intent(out) :: start_step
    logical, intent(out) :: ok
    integer :: unit, ios, best_step
    integer(int64) :: pos, fsize, endpos, expected
    integer(int32) :: nbytes, step32, nx32
    real(real64) :: t
    character(len=4) :: magic
    logical :: exists

    ok = .false.
    start_step = 0
    best_step = -1
    inquire(file=path, exist=exists, size=fsize)
    if (.not. exists .or. fsize < 8_int64) return
    open(newunit=unit, file=path, access='stream', form='unformatted', &
      status='old', action='read', iostat=ios)
    if (ios /= 0) return
    pos = 1_int64
    do while (pos + 7_int64 <= fsize)
      read(unit, pos=pos, iostat=ios) magic
      if (ios /= 0 .or. magic /= 'FNC1') exit
      read(unit, pos=pos + 4_int64, iostat=ios) nbytes
      if (ios /= 0 .or. nbytes < 16) exit
      endpos = pos + 8_int64 + int(nbytes, int64) - 1_int64
      if (endpos > fsize) exit
      read(unit, pos=pos + 8_int64, iostat=ios) step32, nx32, t
      if (ios /= 0) exit
      expected = 16_int64 + 8_int64 * int(nx32, int64)
      if (nx32 == cfg%nx .and. int(nbytes, int64) == expected) then
        if (step32 >= 0 .and. step32 <= target_steps .and. int(step32) > best_step) then
          read(unit, pos=pos + 24_int64, iostat=ios) u
          if (ios == 0) then
            best_step = int(step32)
            start_step = best_step
            ok = .true.
          end if
        end if
      end if
      pos = endpos + 1_int64
    end do
    close(unit)
  end subroutine load_checkpoint

end module checkpoint_mod
F90

cat > src/main.f90 <<'F90'
program restart_solver
  use iso_fortran_env, only: real64
  use config_mod, only: run_config, load_config
  use model_mod, only: initialize_state, advance_state, write_output, enforce_boundaries
  use checkpoint_mod, only: append_checkpoint, load_checkpoint
  implicit none
  type(run_config) :: cfg
  character(len=512) :: config_path, output_path, checkpoint_path
  real(real64), allocatable :: u(:)
  integer :: argc, step, start_step
  logical :: ok, advanced

  argc = command_argument_count()
  if (argc < 3) error stop 'usage: restart_solver <run.nml> <output.csv> <checkpoint.bin>'
  call get_command_argument(1, config_path)
  call get_command_argument(2, output_path)
  call get_command_argument(3, checkpoint_path)

  call load_config(trim(config_path), cfg)
  allocate(u(cfg%nx))
  if (cfg%restart) then
    call load_checkpoint(trim(checkpoint_path), cfg%steps, cfg, u, start_step, ok)
    if (.not. ok) then
      call initialize_state(cfg, u)
      start_step = 0
    end if
  else
    call initialize_state(cfg, u)
    start_step = 0
  end if
  call enforce_boundaries(cfg, u)

  advanced = .false.
  do step = start_step + 1, cfg%steps
    call advance_state(cfg, u)
    advanced = .true.
    if (cfg%checkpoint_interval > 0 .and. mod(step, cfg%checkpoint_interval) == 0) then
      call append_checkpoint(trim(checkpoint_path), step, cfg%dt * real(step, real64), u)
    end if
  end do
  if (advanced) call append_checkpoint(trim(checkpoint_path), cfg%steps, cfg%dt * real(cfg%steps, real64), u)
  call write_output(trim(output_path), cfg, u)
end program restart_solver
F90

make clean all
