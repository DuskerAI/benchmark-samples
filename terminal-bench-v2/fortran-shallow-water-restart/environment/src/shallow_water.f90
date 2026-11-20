program shallow_water_restart
  use iso_fortran_env, only: real32, real64
  implicit none

  integer :: nx, steps, seed, nsplits
  integer, allocatable :: splits(:)
  character(len=256) :: prefix, checkpoint
  character(len=256) :: split_spec
  character(len=32) :: mode

  nx = 48
  steps = 72
  split_spec = '31'
  seed = 19
  prefix = 'out/default'
  mode = 'pair'
  checkpoint = ''
  call parse_args(nx, steps, split_spec, seed, prefix, mode, checkpoint)
  call parse_split_list(trim(split_spec), steps, splits, nsplits)
  if (len_trim(checkpoint) == 0) checkpoint = trim(prefix) // '_restart.chk'

  select case (trim(mode))
  case ('pair')
    call run_pair(nx, steps, splits, nsplits, seed, trim(prefix), trim(checkpoint))
  case ('continuous')
    call run_continuous(nx, steps, seed, trim(prefix))
  case ('checkpoint')
    call run_checkpoint(nx, splits(nsplits), seed, trim(checkpoint))
  case ('resume')
    call run_resume(nx, steps, trim(prefix), trim(checkpoint))
  case default
    error stop 'unsupported mode'
  end select

contains

  subroutine parse_args(nx, steps, split_spec, seed, prefix, mode, checkpoint)
    integer, intent(inout) :: nx, steps, seed
    character(len=*), intent(inout) :: split_spec, prefix, mode, checkpoint
    integer :: i, argc
    character(len=256) :: arg, value

    argc = command_argument_count()
    i = 1
    do while (i <= argc)
      call get_command_argument(i, arg)
      if (i == argc) exit
      call get_command_argument(i + 1, value)
      select case (trim(arg))
      case ('--nx')
        read(value, *) nx
      case ('--steps')
        read(value, *) steps
      case ('--split')
        split_spec = trim(value)
      case ('--seed')
        read(value, *) seed
      case ('--prefix')
        prefix = trim(value)
      case ('--mode')
        mode = trim(value)
      case ('--checkpoint')
        checkpoint = trim(value)
      end select
      i = i + 2
    end do

    if (nx < 8) error stop 'nx must be at least 8'
    if (steps < 1) error stop 'steps must be positive'
  end subroutine parse_args

  subroutine parse_split_list(spec, steps, splits, nsplits)
    character(len=*), intent(in) :: spec
    integer, intent(in) :: steps
    integer, allocatable, intent(out) :: splits(:)
    integer, intent(out) :: nsplits
    integer :: comma
    character(len=256) :: first

    nsplits = 1
    allocate(splits(1))
    comma = index(spec, ',')
    if (comma > 0) then
      first = spec(:comma - 1)
    else
      first = spec
    end if
    read(first, *) splits(1)
    if (splits(1) < 0 .or. splits(1) > steps) error stop 'split must be in [0, steps]'
  end subroutine parse_split_list

  subroutine run_pair(nx, steps, splits, nsplits, seed, prefix, checkpoint)
    integer, intent(in) :: nx, steps, nsplits, seed
    integer, intent(in) :: splits(nsplits)
    character(len=*), intent(in) :: prefix, checkpoint
    real(real64), allocatable :: h(:), u(:), memory(:), lag(:), terrain(:), stress(:), tracer(:), swirl(:)
    real(real64) :: phase, pulse, spin, drift
    integer :: step, loaded_step, loaded_seed, segment, current_step

    allocate(h(nx), u(nx), memory(nx), lag(nx), terrain(nx), stress(nx), tracer(nx), swirl(nx))
    call simulate_from_initial(nx, steps, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    call write_state(trim(prefix) // '_continuous.csv', nx, steps, h, u)

    call initialize_state(nx, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    current_step = 0
    loaded_seed = seed
    do segment = 1, nsplits
      do step = current_step + 1, splits(segment)
        call advance(nx, step, loaded_seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
      end do
      call write_checkpoint(trim(checkpoint), nx, splits(segment), loaded_seed, h, u, memory, &
                            lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
      h = -999.0_real64
      u = 999.0_real64
      memory = 0.0_real64
      lag = 0.0_real64
      terrain = 0.0_real64
      stress = 0.0_real64
      tracer = 0.0_real64
      swirl = 0.0_real64
      call read_checkpoint(trim(checkpoint), nx, loaded_step, loaded_seed, h, u, memory, &
                           lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
      current_step = loaded_step
    end do
    do step = current_step + 1, steps
      call advance(nx, step, loaded_seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    end do
    call write_state(trim(prefix) // '_restarted.csv', nx, steps, h, u)
    deallocate(h, u, memory, lag, terrain, stress, tracer, swirl)
  end subroutine run_pair

  subroutine run_continuous(nx, steps, seed, prefix)
    integer, intent(in) :: nx, steps, seed
    character(len=*), intent(in) :: prefix
    real(real64), allocatable :: h(:), u(:), memory(:), lag(:), terrain(:), stress(:), tracer(:), swirl(:)
    real(real64) :: phase, pulse, spin, drift

    allocate(h(nx), u(nx), memory(nx), lag(nx), terrain(nx), stress(nx), tracer(nx), swirl(nx))
    call simulate_from_initial(nx, steps, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    call write_state(trim(prefix) // '_continuous.csv', nx, steps, h, u)
    deallocate(h, u, memory, lag, terrain, stress, tracer, swirl)
  end subroutine run_continuous

  subroutine run_checkpoint(nx, split, seed, checkpoint)
    integer, intent(in) :: nx, split, seed
    character(len=*), intent(in) :: checkpoint
    real(real64), allocatable :: h(:), u(:), memory(:), lag(:), terrain(:), stress(:), tracer(:), swirl(:)
    real(real64) :: phase, pulse, spin, drift
    integer :: step

    allocate(h(nx), u(nx), memory(nx), lag(nx), terrain(nx), stress(nx), tracer(nx), swirl(nx))
    call initialize_state(nx, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    do step = 1, split
      call advance(nx, step, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    end do
    call write_checkpoint(trim(checkpoint), nx, split, seed, h, u, memory, lag, terrain, &
                          stress, tracer, swirl, phase, pulse, spin, drift)
    deallocate(h, u, memory, lag, terrain, stress, tracer, swirl)
  end subroutine run_checkpoint

  subroutine run_resume(nx, steps, prefix, checkpoint)
    integer, intent(in) :: nx, steps
    character(len=*), intent(in) :: prefix, checkpoint
    real(real64), allocatable :: h(:), u(:), memory(:), lag(:), terrain(:), stress(:), tracer(:), swirl(:)
    real(real64) :: phase, pulse, spin, drift
    integer :: step, loaded_step, loaded_seed

    allocate(h(nx), u(nx), memory(nx), lag(nx), terrain(nx), stress(nx), tracer(nx), swirl(nx))
    call read_checkpoint(trim(checkpoint), nx, loaded_step, loaded_seed, h, u, memory, &
                         lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    do step = loaded_step + 1, steps
      call advance(nx, step, loaded_seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    end do
    call write_state(trim(prefix) // '_restarted.csv', nx, steps, h, u)
    deallocate(h, u, memory, lag, terrain, stress, tracer, swirl)
  end subroutine run_resume

  subroutine simulate_from_initial(nx, steps, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    integer, intent(in) :: nx, steps, seed
    real(real64), intent(out) :: h(nx), u(nx), memory(nx), lag(nx), terrain(nx), stress(nx)
    real(real64), intent(out) :: tracer(nx), swirl(nx), phase, pulse, spin, drift
    integer :: step

    call initialize_state(nx, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    do step = 1, steps
      call advance(nx, step, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    end do
  end subroutine simulate_from_initial

  subroutine initialize_state(nx, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    integer, intent(in) :: nx, seed
    real(real64), intent(out) :: h(nx), u(nx), memory(nx), lag(nx), terrain(nx), stress(nx)
    real(real64), intent(out) :: tracer(nx), swirl(nx), phase, pulse, spin, drift
    integer :: i
    real(real64) :: x

    phase = real(mod(seed, 97), real64) / 97.0_real64
    pulse = 0.002_real64 * sin(0.005_real64 * real(seed, real64)) + 0.001_real64 * cos(phase)
    spin = 0.0013_real64 * cos(0.009_real64 * real(seed, real64)) - 0.0007_real64 * sin(phase)
    drift = 0.0009_real64 * sin(0.004_real64 * real(seed, real64)) + 0.0006_real64 * cos(2.0_real64 * phase)
    do i = 1, nx
      x = real(i - 1, real64) / real(nx, real64)
      h(i) = 1.0_real64 + 0.08_real64 * sin(6.283185307179586_real64 * (x + phase)) &
             + 0.015_real64 * cos(18.84955592153876_real64 * x)
      u(i) = 0.03_real64 * cos(6.283185307179586_real64 * (x - 0.5_real64 * phase))
      memory(i) = 0.006_real64 * sin(12.566370614359172_real64 * x + 0.37_real64 * phase) &
                  - 0.004_real64 * cos(25.132741228718345_real64 * x - 0.19_real64 * phase)
      lag(i) = 0.0035_real64 * cos(31.41592653589793_real64 * x + 0.13_real64 * phase) &
               + 0.0018_real64 * sin(43.982297150257104_real64 * x - 0.007_real64 * real(seed, real64))
      terrain(i) = 0.0042_real64 * sin(9.42477796076938_real64 * x + 0.23_real64 * phase) &
                   - 0.0026_real64 * cos(15.707963267948966_real64 * x - 0.003_real64 * real(seed, real64))
      stress(i) = 0.0029_real64 * sin(21.991148575128552_real64 * x + 0.17_real64 * phase) &
                   + 0.0011_real64 * cos(28.274333882308138_real64 * x - 0.002_real64 * real(seed, real64))
      tracer(i) = 0.0016_real64 * sin(34.55751918948772_real64 * x + pulse) &
                  - 0.0012_real64 * cos(18.2212373908208_real64 * x - spin)
      swirl(i) = 0.0019_real64 * cos(40.840704496667314_real64 * x + drift) &
                 + 0.0014_real64 * sin(23.561944901923447_real64 * x - phase)
    end do
  end subroutine initialize_state

  subroutine advance(nx, step, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    integer, intent(in) :: nx, step, seed
    real(real64), intent(in) :: terrain(nx)
    real(real64), intent(inout) :: h(nx), u(nx), memory(nx), lag(nx), stress(nx), tracer(nx), swirl(nx)
    real(real64), intent(inout) :: phase, pulse, spin, drift
    real(real64), allocatable :: hn(:), un(:), mn(:), ln(:), sn(:), tn(:), wn(:)
    integer :: i, im, ip
    real(real64) :: wave, source, phase_increment, cross_cell, lag_flux, terrain_flux

    allocate(hn(nx), un(nx), mn(nx), ln(nx), sn(nx), tn(nx), wn(nx))
    phase_increment = 0.00073_real64 * sin(0.017_real64 * real(seed + step, real64)) &
                       + 0.00031_real64 * cos(0.011_real64 * real(seed - 2 * step, real64))
    phase = phase + phase_increment
    pulse = pulse + 0.00019_real64 * sin(0.013_real64 * real(seed + 7 * step, real64)) &
            - 0.00011_real64 * cos(0.019_real64 * real(seed - step, real64))
    spin = 0.997_real64 * spin + 0.00023_real64 * sin(phase + 0.021_real64 * real(step, real64)) &
           + 0.00014_real64 * cos(pulse + 0.017_real64 * real(seed, real64))
    drift = 0.991_real64 * drift + 0.00003_real64 * sum(swirl) / real(nx, real64) &
            + 0.00012_real64 * sin(spin + 0.015_real64 * real(step, real64))
    do i = 1, nx
      im = merge(nx, i - 1, i == 1)
      ip = merge(1, i + 1, i == nx)
      wave = sin(0.071_real64 * real(seed + 3 * step + i, real64))
      source = cos(0.043_real64 * real(2 * seed - step + 5 * i, real64))
      cross_cell = memory(i) - 0.5_real64 * (memory(im) + memory(ip))
      lag_flux = lag(ip) - 0.35_real64 * lag(im) + 0.15_real64 * lag(i)
      terrain_flux = terrain(ip) - 0.4_real64 * terrain(im) + 0.1_real64 * terrain(i)
      hn(i) = h(i) - 0.055_real64 * (u(ip) - u(im)) + 0.0025_real64 * wave &
                + 0.0011_real64 * memory(im) - 0.0007_real64 * phase + 0.0009_real64 * lag(i) &
               + 0.0006_real64 * terrain_flux + 0.00045_real64 * stress(ip) + 0.0003_real64 * pulse &
                + 0.00085_real64 * tracer(im) - 0.00022_real64 * spin &
                + 0.00051_real64 * swirl(ip) + 0.00027_real64 * drift
      un(i) = 0.992_real64 * u(i) - 0.047_real64 * (h(ip) - h(im)) + 0.0017_real64 * source &
                + 0.0023_real64 * cross_cell + 0.0005_real64 * phase + 0.0012_real64 * lag_flux &
               - 0.0008_real64 * terrain(i) - 0.00055_real64 * stress(im) + 0.00025_real64 * pulse &
                + 0.00065_real64 * tracer(ip) + 0.00019_real64 * spin &
                - 0.00042_real64 * swirl(im) + 0.00031_real64 * drift
      mn(i) = 0.965_real64 * memory(i) + 0.021_real64 * (h(ip) - h(im)) &
                + 0.013_real64 * u(i) + 0.002_real64 * phase + 0.003_real64 * lag(i) &
               + 0.0015_real64 * terrain_flux + 0.0011_real64 * stress(i) + 0.0014_real64 * tracer(i)
      ln(i) = 0.941_real64 * lag(i) + 0.014_real64 * (u(ip) - u(im)) &
                + 0.009_real64 * memory(ip) - 0.0013_real64 * phase &
                + 0.0002_real64 * sin(0.029_real64 * real(seed + step * i, real64)) &
               + 0.0008_real64 * stress(ip) - 0.0006_real64 * tracer(im)
      sn(i) = 0.953_real64 * stress(i) + 0.011_real64 * (memory(i) - lag(im)) &
               + 0.004_real64 * terrain_flux + 0.0003_real64 * pulse &
              + 0.00017_real64 * sin(0.037_real64 * real(seed + i * step, real64)) &
               + 0.0007_real64 * tracer(ip) - 0.00029_real64 * swirl(im)
      tn(i) = 0.948_real64 * tracer(i) + 0.012_real64 * (stress(ip) - memory(im)) &
              + 0.006_real64 * lag_flux - 0.003_real64 * terrain_flux + 0.00041_real64 * spin &
              + 0.00033_real64 * swirl(i)
      wn(i) = 0.936_real64 * swirl(i) + 0.010_real64 * (u(ip) - u(im)) &
              + 0.006_real64 * (h(ip) - 2.0_real64 * h(i) + h(im)) + 0.002_real64 * terrain_flux &
              + 0.0017_real64 * tracer(i) + 0.0003_real64 * drift
    end do
    h = hn
    u = un
    memory = mn
    lag = ln
    stress = sn
    tracer = tn
    swirl = wn
    deallocate(hn, un, mn, ln, sn, tn, wn)
  end subroutine advance

  subroutine write_checkpoint(path, nx, step, seed, h, u, memory, lag, terrain, stress, tracer, swirl, phase, pulse, spin, drift)
    character(len=*), intent(in) :: path
    integer, intent(in) :: nx, step, seed
    real(real64), intent(in) :: h(nx), u(nx), memory(nx), lag(nx), terrain(nx), stress(nx)
    real(real64), intent(in) :: tracer(nx), swirl(nx), phase, pulse, spin, drift
    integer :: unit, i, shifted, previous

    open(newunit=unit, file=path, status='replace', action='write')
    write(unit, '(3(I0,1X),ES26.17,1X,ES26.17,1X,ES26.17,1X,ES26.17)') &
      nx, step - 1, seed, phase, pulse - 0.001_real64, spin * 0.5_real64, drift - 0.002_real64
    do i = 1, nx
      shifted = merge(1, i + 1, i == nx)
      previous = merge(nx, i - 1, i == 1)
      write(unit, '(I0,1X,ES26.17,1X,ES26.17,1X,ES14.6,1X,ES14.6,1X,ES14.6,1X,ES14.6,1X,ES14.6,1X,ES14.6)') &
        i, h(i), u(shifted), real(memory(i), kind=real32), real(lag(previous), kind=real32), &
        real(terrain(shifted), kind=real32), real(stress(previous), kind=real32), &
        real(tracer(previous), kind=real32), real(swirl(shifted), kind=real32)
    end do
    close(unit)
  end subroutine write_checkpoint

  subroutine read_checkpoint(path, expected_nx, step, seed, h, u, memory, lag, terrain, &
                             stress, tracer, swirl, phase, pulse, spin, drift)
    character(len=*), intent(in) :: path
    integer, intent(in) :: expected_nx
    integer, intent(out) :: step, seed
    real(real64), intent(out) :: h(expected_nx), u(expected_nx), memory(expected_nx), lag(expected_nx)
    real(real64), intent(out) :: terrain(expected_nx), stress(expected_nx), tracer(expected_nx), swirl(expected_nx)
    real(real64), intent(out) :: phase, pulse, spin, drift
    integer :: unit, nx_file, i, idx
    real(real64) :: phase_file, pulse_file, spin_file, drift_file

    open(newunit=unit, file=path, status='old', action='read')
    read(unit, *) nx_file, step, seed, phase_file, pulse_file, spin_file, drift_file
    if (nx_file /= expected_nx) error stop 'restart grid does not match run grid'
    seed = seed + 17
    phase = 0.0_real64
    pulse = 0.0_real64
    spin = 0.0_real64
    drift = 0.0_real64
    do i = 1, expected_nx
      read(unit, *) idx, h(i), u(i), memory(i), lag(i), terrain(i), stress(i), tracer(i), swirl(i)
      if (idx /= i) error stop 'restart cell order is invalid'
      tracer(i) = 0.0_real64
      swirl(i) = 0.0_real64
    end do
    close(unit)
  end subroutine read_checkpoint

  subroutine write_state(path, nx, step, h, u)
    character(len=*), intent(in) :: path
    integer, intent(in) :: nx, step
    real(real64), intent(in) :: h(nx), u(nx)
    integer :: unit, i
    real(real64) :: mass, momentum, energy

    mass = sum(h)
    momentum = sum(h * u)
    energy = sum(0.5_real64 * h * u * u + 0.5_real64 * h * h)
    open(newunit=unit, file=path, status='replace', action='write')
    write(unit, '(A)') 'record,step,index,height,velocity,mass,momentum,energy'
    write(unit, '(A,I0,A,ES26.17,A,ES26.17,A,ES26.17)') 'summary,', step, ',0,0,0,', mass, ',', momentum, ',', energy
    do i = 1, nx
      write(unit, '(A,I0,A,I0,A,ES26.17,A,ES26.17,A)') 'cell,', step, ',', i, ',', h(i), ',', u(i), ',0,0,0'
    end do
    close(unit)
  end subroutine write_state

end program shallow_water_restart
