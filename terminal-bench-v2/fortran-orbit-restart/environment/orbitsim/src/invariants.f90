module invariants_mod
  use precision_mod
  use model_mod
  implicit none
contains
  subroutine compute_invariants(bodies, g, energy, angular_momentum)
    type(body_state), intent(in) :: bodies(:)
    real(rk), intent(in) :: g
    real(rk), intent(out) :: energy, angular_momentum
    integer :: i, j, n
    real(rk) :: dx, dy, r

    n = size(bodies)
    energy = 0.0_rk
    angular_momentum = 0.0_rk
    do i = 1, n
       energy = energy + 0.5_rk * bodies(i)%mass * (bodies(i)%vx * bodies(i)%vx + bodies(i)%vy * bodies(i)%vy)
       angular_momentum = angular_momentum + bodies(i)%mass * (bodies(i)%x * bodies(i)%vy - bodies(i)%y * bodies(i)%vx)
    end do
    do i = 1, n - 1
       do j = i + 1, n
          dx = bodies(i)%x - bodies(j)%x
          dy = bodies(i)%y - bodies(j)%y
          r = sqrt(dx * dx + dy * dy) + 1.0e-5_rk
          energy = energy - g * bodies(i)%mass * bodies(j)%mass / r
       end do
    end do
  end subroutine compute_invariants
end module invariants_mod
