program orbitsim
  use precision_mod
  use model_mod
  use config_mod
  use integrator_mod
  use checkpoint_mod
  use output_mod
  implicit none

  type(case_config) :: cfg
  type(body_state), allocatable :: bodies(:)
  character(len=512) :: case_path, outdir, arg, checkpoint_path, resume_path
  integer :: argc, iarg, checkpoint_at, step, traj_unit, inv_unit
  real(rk) :: time
  logical :: have_checkpoint, have_resume

  argc = command_argument_count()
  if (argc < 2) stop 2
  call get_command_argument(1, case_path)
  call get_command_argument(2, outdir)

  checkpoint_at = -1
  have_checkpoint = .false.
  have_resume = .false.
  iarg = 3
  do while (iarg <= argc)
     call get_command_argument(iarg, arg)
     select case (trim(arg))
     case ('--checkpoint-at')
        iarg = iarg + 1
        call get_command_argument(iarg, arg)
        read(arg, *) checkpoint_at
        have_checkpoint = .true.
     case ('--resume')
        iarg = iarg + 1
        call get_command_argument(iarg, resume_path)
        have_resume = .true.
     case default
        stop 3
     end select
     iarg = iarg + 1
  end do

  call read_case(trim(case_path), cfg)
  call execute_command_line('mkdir -p "'//trim(outdir)//'"')

  if (have_resume) then
     call read_checkpoint(trim(resume_path), step, time, bodies)
  else
     allocate(bodies(size(cfg%bodies)))
     bodies = cfg%bodies
     step = 0
     time = 0.0_rk
  end if

  call open_outputs(trim(outdir), traj_unit, inv_unit)
  call record_outputs(traj_unit, inv_unit, step, time, bodies, cfg%g)

  if (have_checkpoint .and. .not. have_resume) then
     do while (step < checkpoint_at)
        call advance(bodies, cfg%g, cfg%dt)
        step = step + 1
        time = time + cfg%dt
        if (mod(step, cfg%output_every) == 0) call record_outputs(traj_unit, inv_unit, step, time, bodies, cfg%g)
     end do
     checkpoint_path = trim(outdir)//'/checkpoint.dat'
     call write_checkpoint(trim(checkpoint_path), step, time, bodies)
     if (mod(step, cfg%output_every) /= 0) call record_outputs(traj_unit, inv_unit, step, time, bodies, cfg%g)
     call write_final(trim(outdir), bodies)
     close(traj_unit)
     close(inv_unit)
     stop
  end if

  do while (step < cfg%nsteps)
     call advance(bodies, cfg%g, cfg%dt)
     step = step + 1
     time = time + cfg%dt
     if (mod(step, cfg%output_every) == 0 .or. step == cfg%nsteps) then
        call record_outputs(traj_unit, inv_unit, step, time, bodies, cfg%g)
     end if
  end do

  call write_final(trim(outdir), bodies)
  close(traj_unit)
  close(inv_unit)
end program orbitsim
