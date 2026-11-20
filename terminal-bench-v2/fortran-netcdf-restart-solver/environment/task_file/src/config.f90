module config_mod
  use iso_fortran_env, only: real64
  implicit none

  type :: run_config
    integer :: nx = 0
    integer :: steps = 0
    integer :: checkpoint_interval = 0
    real(real64) :: dt = 0.0_real64
    real(real64) :: diffusion = 0.0_real64
    real(real64) :: left_bc = 0.0_real64
    real(real64) :: right_bc = 0.0_real64
    logical :: restart = .false.
  end type run_config

contains

  subroutine load_config(path, cfg)
    character(len=*), intent(in) :: path
    type(run_config), intent(out) :: cfg
    integer :: unit, ios
    integer :: nx, steps, checkpoint_interval
    real(real64) :: dt, diffusion, left_bc, right_bc
    logical :: restart
    namelist /run/ nx, steps, dt, diffusion, left_bc, right_bc, checkpoint_interval, restart

    nx = 0
    steps = 0
    checkpoint_interval = 0
    dt = 0.0_real64
    diffusion = 0.0_real64
    left_bc = 0.0_real64
    right_bc = 0.0_real64
    restart = .false.

    open(newunit=unit, file=path, status='old', action='read', iostat=ios)
    if (ios /= 0) error stop 'could not open namelist'
    read(unit, nml=run, iostat=ios)
    close(unit)
    if (ios /= 0) error stop 'could not read namelist'
    if (nx < 3 .or. steps < 0 .or. dt <= 0.0_real64 .or. diffusion < 0.0_real64) error stop 'invalid run'

    cfg%nx = nx
    cfg%steps = steps
    cfg%checkpoint_interval = checkpoint_interval
    cfg%dt = dt
    cfg%diffusion = diffusion
    cfg%left_bc = left_bc
    cfg%right_bc = right_bc
    cfg%restart = restart
  end subroutine load_config

end module config_mod
