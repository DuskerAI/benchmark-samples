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

  subroutine advance_state(cfg, u)
    type(run_config), intent(in) :: cfg
    real(real64), intent(inout) :: u(:)
    real(real64), allocatable :: next(:)
    real(real64) :: r
    integer :: i, n
    n = size(u)
    allocate(next(n))
    r = cfg%diffusion * cfg%dt * real((n - 1) * (n - 1), real64)
    next(1) = u(1)
    next(n) = u(n)
    do i = 2, n - 1
      next(i) = u(i) + r * (u(i - 1) - 2.0_real64 * u(i) + u(i + 1))
    end do
    u = next
    deallocate(next)
  end subroutine advance_state

  subroutine write_output(path, cfg, u)
    character(len=*), intent(in) :: path
    type(run_config), intent(in) :: cfg
    real(real64), intent(in) :: u(:)
    integer :: unit, i, n
    real(real64) :: x
    n = size(u)
    open(newunit=unit, file=path, status='replace', action='write')
    write(unit,'(a)') 'index,x,u'
    do i = 1, n
      x = real(i - 1, real64) / real(n - 1, real64)
      write(unit,'(i0,a,es25.17,a,es25.17)') i, ',', x, ',', u(i)
    end do
    close(unit)
  end subroutine write_output

end module model_mod
