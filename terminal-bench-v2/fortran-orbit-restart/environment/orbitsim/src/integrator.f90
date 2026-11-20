module integrator_mod
  use precision_mod
  use model_mod
  use forces_mod
  implicit none
contains
  subroutine advance(bodies, g, dt)
    type(body_state), intent(inout) :: bodies(:)
    real(rk), intent(in) :: g, dt
    real(rk), allocatable :: ax(:), ay(:)
    integer :: i, n

    n = size(bodies)
    allocate(ax(n), ay(n))
    call accelerations(bodies, g, ax, ay)
    do i = 1, n
       bodies(i)%vx = bodies(i)%vx + 0.5_rk * dt * ax(i)
       bodies(i)%vy = bodies(i)%vy + 0.5_rk * dt * ay(i)
       bodies(i)%x = bodies(i)%x + dt * bodies(i)%vx
       bodies(i)%y = bodies(i)%y + dt * bodies(i)%vy
    end do
    call accelerations(bodies, g, ax, ay)
    do i = 1, n
       bodies(i)%vx = bodies(i)%vx + 0.5_rk * dt * ax(i)
       bodies(i)%vy = bodies(i)%vy + 0.5_rk * dt * ay(i)
    end do
    deallocate(ax, ay)
  end subroutine advance
end module integrator_mod
