module checkpoint_mod
  use precision_mod
  use model_mod
  implicit none
contains
  subroutine write_checkpoint(path, step, time, bodies)
    character(len=*), intent(in) :: path
    integer, intent(in) :: step
    real(rk), intent(in) :: time
    type(body_state), intent(in) :: bodies(:)
    integer :: unit, i

    open(newunit=unit, file=trim(path), status='replace', action='write')
    write(unit, '(I0,1X,F12.6,1X,I0)') step + 1, time, size(bodies)
    do i = 1, size(bodies)
       write(unit, '(5(1X,ES16.8))') bodies(i)%mass, bodies(i)%x, bodies(i)%y, bodies(i)%vx, bodies(i)%vy
    end do
    close(unit)
  end subroutine write_checkpoint

  subroutine read_checkpoint(path, step, time, bodies)
    character(len=*), intent(in) :: path
    integer, intent(out) :: step
    real(rk), intent(out) :: time
    type(body_state), allocatable, intent(out) :: bodies(:)
    integer :: unit, i, n

    open(newunit=unit, file=trim(path), status='old', action='read')
    read(unit, *) step, time, n
    allocate(bodies(n))
    do i = 1, n
       read(unit, *) bodies(i)%mass, bodies(i)%x, bodies(i)%y, bodies(i)%vx, bodies(i)%vy
    end do
    close(unit)
  end subroutine read_checkpoint
end module checkpoint_mod
