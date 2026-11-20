program restart_solver
  use iso_fortran_env, only: real64
  use config_mod, only: run_config, load_config
  use model_mod, only: initialize_state, advance_state, write_output
  use checkpoint_mod, only: append_checkpoint, load_checkpoint
  implicit none
  type(run_config) :: cfg
  character(len=512) :: config_path, output_path, checkpoint_path
  real(real64), allocatable :: u(:)
  integer :: argc, step, start_step
  logical :: ok, wrote_final

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

  wrote_final = .false.
  do step = start_step + 1, cfg%steps
    call advance_state(cfg, u)
    if (cfg%checkpoint_interval > 0 .and. mod(step, cfg%checkpoint_interval) == 0) then
      call append_checkpoint(trim(checkpoint_path), step, cfg%dt * real(step, real64), u)
      if (step == cfg%steps) wrote_final = .true.
    end if
  end do
  if (.not. wrote_final) call append_checkpoint(trim(checkpoint_path), cfg%steps, cfg%dt * real(cfg%steps, real64), u)
  call write_output(trim(output_path), cfg, u)
end program restart_solver
