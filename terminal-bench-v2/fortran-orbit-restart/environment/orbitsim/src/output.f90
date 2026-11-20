module output_mod
  use precision_mod
  use model_mod
  use invariants_mod
  implicit none
contains
  subroutine open_outputs(outdir, traj_unit, inv_unit)
    character(len=*), intent(in) :: outdir
    integer, intent(out) :: traj_unit, inv_unit
    open(newunit=traj_unit, file=trim(outdir)//'/trajectory.tsv', status='replace', action='write')
    open(newunit=inv_unit, file=trim(outdir)//'/invariants.tsv', status='replace', action='write')
    write(traj_unit, '(A)') 'step time body mass x y vx vy'
    write(inv_unit, '(A)') 'step time energy angular_momentum'
  end subroutine open_outputs

  subroutine record_outputs(traj_unit, inv_unit, step, time, bodies, g)
    integer, intent(in) :: traj_unit, inv_unit, step
    real(rk), intent(in) :: time, g
    type(body_state), intent(in) :: bodies(:)
    integer :: i
    real(rk) :: energy, angular_momentum

    do i = 1, size(bodies)
       write(traj_unit, '(I0,1X,ES24.16,1X,I0,5(1X,ES24.16))') &
          step, time, i, bodies(i)%mass, bodies(i)%x, bodies(i)%y, &
          bodies(i)%vx, bodies(i)%vy
    end do
    call compute_invariants(bodies, g, energy, angular_momentum)
    write(inv_unit, '(I0,3(1X,ES24.16))') step, time, energy, angular_momentum
  end subroutine record_outputs

  subroutine write_final(outdir, bodies)
    character(len=*), intent(in) :: outdir
    type(body_state), intent(in) :: bodies(:)
    integer :: unit, i
    open(newunit=unit, file=trim(outdir)//'/final_state.tsv', status='replace', action='write')
    write(unit, '(A)') 'body mass x y vx vy'
    do i = 1, size(bodies)
       write(unit, '(I0,5(1X,ES24.16))') i, bodies(i)%mass, bodies(i)%x, bodies(i)%y, bodies(i)%vx, bodies(i)%vy
    end do
    close(unit)
  end subroutine write_final
end module output_mod
