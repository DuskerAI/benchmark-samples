module forces_mod
  use precision_mod
  use model_mod
  implicit none
contains
  subroutine accelerations(bodies, g, ax, ay)
    type(body_state), intent(in) :: bodies(:)
    real(rk), intent(in) :: g
    real(rk), intent(out) :: ax(size(bodies)), ay(size(bodies))
    integer :: i, j, n
    real(rk) :: dx, dy, r2, invr3

    n = size(bodies)
    ax = 0.0_rk
    ay = 0.0_rk
    do i = 1, n
       do j = 1, n
          if (i /= j) then
             dx = bodies(j)%x - bodies(i)%x
             dy = bodies(j)%y - bodies(i)%y
              r2 = dx * dx + dy * dy + 1.0e-6_rk
             invr3 = 1.0_rk / (r2 * sqrt(r2))
             ax(i) = ax(i) + g * bodies(j)%mass * dx * invr3
             ay(i) = ay(i) + g * bodies(j)%mass * dy * invr3
          end if
       end do
    end do
  end subroutine accelerations
end module forces_mod
