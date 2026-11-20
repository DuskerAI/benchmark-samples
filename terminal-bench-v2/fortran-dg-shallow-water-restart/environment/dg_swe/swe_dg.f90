program swe_dg
  implicit none
  integer, parameter :: dp = kind(1.0d0)
  integer :: argc, cells, steps, checkpoint_interval, start_step, n
  real(dp) :: cfl, gravity, slope, hump, dry_floor, time
  character(len=256) :: cfg, out, chk
  real(dp), allocatable :: h0(:), h1(:), q0(:), q1(:)
  logical :: restart

  argc = command_argument_count()
  if (argc /= 3) stop 2
  call get_command_argument(1, cfg)
  call get_command_argument(2, out)
  call get_command_argument(3, chk)

  cells = 40; steps = 20; cfl = 0.16_dp; gravity = 9.81_dp
  slope = 0.0_dp; hump = 0.04_dp; dry_floor = 1.0e-3_dp
  checkpoint_interval = 0; restart = .false.
  call read_config(trim(cfg), cells, steps, cfl, gravity, slope, hump, dry_floor, checkpoint_interval, restart)
  allocate(h0(cells), h1(cells), q0(cells), q1(cells))

  call initialize(cells, hump, dry_floor, h0, h1, q0, q1)
  start_step = 0; time = 0.0_dp
  if (restart) then
     call load_checkpoint(trim(chk), cells, steps, start_step, time, h0, h1, q0, q1)
     start_step = 0
     h1 = 0.0_dp
     q1 = 0.0_dp
  end if

  do n = start_step + 1, steps
     call advance(cells, cfl, gravity, slope, dry_floor, h0, h1, q0, q1)
     time = time + cfl / real(cells, dp)
     if (checkpoint_interval > 0) then
        if (mod(n, checkpoint_interval) == 0 .or. n == steps) then
           call write_checkpoint(trim(chk), cells, n, time, h0, h1, q0, q1)
        end if
     end if
  end do
  call write_csv(trim(out), cells, h0, h1, q0, q1)

contains
  subroutine read_config(path, cells, steps, cfl, gravity, slope, hump, dry_floor, interval, restart)
    character(len=*), intent(in) :: path
    integer, intent(inout) :: cells, steps, interval
    real(dp), intent(inout) :: cfl, gravity, slope, hump, dry_floor
    logical, intent(inout) :: restart
    character(len=512) :: line, key, val
    integer :: unit, ios, p
    open(newunit=unit, file=path, status='old', action='read')
    do
       read(unit, '(A)', iostat=ios) line
       if (ios /= 0) exit
       line = adjustl(line)
       if (len_trim(line) == 0 .or. line(1:1) == '#') cycle
       p = index(line, '=')
       if (p <= 0) cycle
       key = adjustl(line(:p-1)); val = adjustl(line(p+1:))
       select case(trim(key))
       case('cells'); read(val,*) cells
       case('steps'); read(val,*) steps
       case('cfl'); read(val,*) cfl
       case('gravity'); read(val,*) gravity
       case('slope'); read(val,*) slope
       case('hump'); read(val,*) hump
       case('dry_floor'); read(val,*) dry_floor
       case('checkpoint_interval'); read(val,*) interval
       case('restart'); restart = trim(val) == 'true' .or. trim(val) == '1'
       end select
    end do
    close(unit)
  end subroutine

  subroutine initialize(cells, hump, dry_floor, h0, h1, q0, q1)
    integer, intent(in) :: cells
    real(dp), intent(in) :: hump, dry_floor
    real(dp), intent(out) :: h0(:), h1(:), q0(:), q1(:)
    integer :: i
    real(dp) :: x, dx, g1, g2
    dx = 1.0_dp / real(cells, dp)
    do i = 1, cells
       x = (real(i, dp) - 0.5_dp) * dx
       g1 = exp(-((x - 0.31_dp) / 0.075_dp)**2)
       g2 = exp(-((x - 0.73_dp) / 0.060_dp)**2)
       h0(i) = dry_floor + 0.018_dp + hump * g1 - 0.014_dp * g2
       if (h0(i) < 0.55_dp * dry_floor) h0(i) = 0.55_dp * dry_floor
       h1(i) = 0.5_dp * hump * (exp(-((x + 0.5_dp*dx - 0.31_dp)/0.075_dp)**2) - exp(-((x - 0.5_dp*dx - 0.31_dp)/0.075_dp)**2))
       q0(i) = 0.006_dp * sin(6.283185307179586_dp * x) * max(h0(i) - dry_floor, 0.0_dp)
       q1(i) = 0.25_dp * q0(i) * cos(6.283185307179586_dp * x)
    end do
  end subroutine

  subroutine advance(cells, cfl, gravity, slope, dry_floor, h0, h1, q0, q1)
    integer, intent(in) :: cells
    real(dp), intent(in) :: cfl, gravity, slope, dry_floor
    real(dp), intent(inout) :: h0(:), h1(:), q0(:), q1(:)
    real(dp), allocatable :: fh(:), fq(:), nh(:), nq(:)
    integer :: i, l, r
    real(dp) :: dx, hl, hr, ul, ur, ql, qr, flh, frh, flq, frq, a, mass
    dx = 1.0_dp / real(cells, dp)
    allocate(fh(0:cells), fq(0:cells), nh(cells), nq(cells))
    do i = 0, cells
       l = max(1, i); r = min(cells, i + 1)
       hl = max(dry_floor, h0(l) + 0.5_dp*h1(l)); hr = max(dry_floor, h0(r) - 0.5_dp*h1(r))
       ql = q0(l) + 0.5_dp*q1(l); qr = q0(r) - 0.5_dp*q1(r)
       ul = ql / hl; ur = qr / hr
       flh = ql; frh = qr
       flq = ql*ul + 0.5_dp*gravity*hl*hl; frq = qr*ur + 0.5_dp*gravity*hr*hr
       a = max(abs(ul) + sqrt(gravity*hl), abs(ur) + sqrt(gravity*hr))
       fh(i) = 0.5_dp*(flh + frh) - 0.5_dp*a*(hr - hl)
       fq(i) = 0.5_dp*(flq + frq) - 0.5_dp*a*(qr - ql)
    end do
    do i = 1, cells
       nh(i) = h0(i) - cfl * (fh(i) - fh(i-1))
       nq(i) = q0(i) - cfl * (fq(i) - fq(i-1)) + cfl * slope * h0(i) * dx
    end do
    do i = 1, cells
       h0(i) = max(dry_floor, nh(i))
       q0(i) = nq(i)
       h1(i) = limited_slope(h0(mod(i,cells)+1) - h0(i), h0(i) - h0(mod(i-2+cells,cells)+1))
       q1(i) = limited_slope(q0(mod(i,cells)+1) - q0(i), q0(i) - q0(mod(i-2+cells,cells)+1))
       if (h0(i) <= 1.05_dp*dry_floor) q0(i) = 0.0_dp
    end do
    mass = sum(h0) / real(cells, dp)
    deallocate(fh, fq, nh, nq)
  end subroutine

  real(dp) function limited_slope(a, b)
    real(dp), intent(in) :: a, b
    if (a*b <= 0.0_dp) then
       limited_slope = 0.0_dp
    else
       limited_slope = sign(min(abs(a), abs(b), 0.5_dp*(abs(a)+abs(b))), a)
    end if
  end function

  real(dp) function checksum(cells, step, time, h0, h1, q0, q1)
    integer, intent(in) :: cells, step
    real(dp), intent(in) :: time, h0(:), h1(:), q0(:), q1(:)
    integer :: i
    checksum = real(step, dp) * 0.125_dp + time
    do i = 1, cells
       checksum = checksum + h0(i)*(1.0_dp+i) + h1(i)*(3.0_dp+i) + q0(i)*(5.0_dp+i) + q1(i)*(7.0_dp+i)
    end do
  end function

  subroutine write_checkpoint(path, cells, step, time, h0, h1, q0, q1)
    character(len=*), intent(in) :: path
    integer, intent(in) :: cells, step
    real(dp), intent(in) :: time, h0(:), h1(:), q0(:), q1(:)
    integer :: unit, i
    open(newunit=unit, file=path, status='unknown', position='append', action='write')
    write(unit,'(A,1X,I0,1X,I0,1X,ES25.16,1X,ES25.16)') 'BEGIN DGSW1', cells, step, time, checksum(cells, step, time, h0, h1, q0, q1)
    do i = 1, cells
       write(unit,'(I0,4(1X,ES25.16))') i, h0(i), h1(i), q0(i), q1(i)
    end do
    write(unit,'(A,1X,ES25.16)') 'END', checksum(cells, step, time, h0, h1, q0, q1)
    close(unit)
  end subroutine

  subroutine load_checkpoint(path, cells, target, best_step, best_time, h0, h1, q0, q1)
    character(len=*), intent(in) :: path
    integer, intent(in) :: cells, target
    integer, intent(out) :: best_step
    real(dp), intent(out) :: best_time, h0(:), h1(:), q0(:), q1(:)
    integer :: unit, ios, c, step, i, idx
    real(dp) :: t, want, got, tail, th0(cells), th1(cells), tq0(cells), tq1(cells)
    character(len=32) :: tag, magic
    best_step = 0; best_time = 0.0_dp
    open(newunit=unit, file=path, status='old', action='read', iostat=ios)
    if (ios /= 0) return
    do
       read(unit, *, iostat=ios) tag, magic, c, step, t, want
       if (ios /= 0) exit
       if (trim(tag) /= 'BEGIN' .or. trim(magic) /= 'DGSW1' .or. c /= cells) exit
       do i = 1, cells
          read(unit, *, iostat=ios) idx, th0(i), th1(i), tq0(i), tq1(i)
          if (ios /= 0 .or. idx /= i) exit
       end do
       if (ios /= 0) exit
       read(unit, *, iostat=ios) tag, tail
       if (ios /= 0 .or. trim(tag) /= 'END') exit
       got = checksum(cells, step, t, th0, th1, tq0, tq1)
       if (abs(got - want) < 1.0e-9_dp .and. abs(got - tail) < 1.0e-9_dp .and. step <= target) then
          best_step = step; best_time = t; h0 = th0; h1 = th1; q0 = tq0; q1 = tq1
       end if
    end do
    close(unit)
  end subroutine

  subroutine write_csv(path, cells, h0, h1, q0, q1)
    character(len=*), intent(in) :: path
    integer, intent(in) :: cells
    real(dp), intent(in) :: h0(:), h1(:), q0(:), q1(:)
    integer :: unit, i
    open(newunit=unit, file=path, status='replace', action='write')
    write(unit,'(A)') 'cell,x,h_avg,h_slope,q_avg,q_slope'
    do i = 1, cells
       write(unit,'(I0,5(",",ES25.16))') i, (real(i,dp)-0.5_dp)/real(cells,dp), h0(i), h1(i), q0(i), q1(i)
    end do
    close(unit)
  end subroutine
end program
