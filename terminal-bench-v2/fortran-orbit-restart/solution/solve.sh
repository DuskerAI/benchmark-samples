#!/bin/bash
set -euo pipefail

cd /app

perl -0pi -e 's/selected_real_kind\(6, 37\)/selected_real_kind(15, 307)/g' orbitsim/src/precision.f90

cat > orbitsim/src/model.f90 <<'F90'
module model_mod
  use precision_mod
  implicit none
  type :: body_state
     real(rk) :: mass, x, y, vx, vy
  end type body_state
  type :: burn_event
     integer :: step, body
     real(rk) :: dvx, dvy
  end type burn_event
  type :: tidal_point
      real(rk) :: x, y, mass
  end type tidal_point
  type :: mass_event
     integer :: step, body
     real(rk) :: factor
  end type mass_event
  type :: drag_zone
     real(rk) :: xmin, xmax, ymin, ymax, gamma
  end type drag_zone
  type :: case_config
       integer :: nsteps, output_every, recenter_every, epoch_step, substeps
       real(rk) :: dt, g, epoch_time
       logical :: have_walls, have_merge
       real(rk) :: xmin, xmax, ymin, ymax, merge_radius
       type(body_state), allocatable :: bodies(:)
       type(burn_event), allocatable :: burns(:)
       type(mass_event), allocatable :: mass_changes(:)
       type(tidal_point), allocatable :: tidal_points(:)
       type(drag_zone), allocatable :: drag_zones(:)
  end type case_config
end module model_mod
F90

cat > orbitsim/src/config.f90 <<'F90'
module config_mod
  use precision_mod
  use model_mod
  implicit none
contains
  subroutine read_case(path, cfg)
    character(len=*), intent(in) :: path
    type(case_config), intent(out) :: cfg
    character(len=64) :: key
    character(len=256) :: line
    integer :: unit, n, i, ios

    cfg%recenter_every = 0
    cfg%epoch_step = 0
    cfg%epoch_time = 0.0_rk
    cfg%substeps = 1
    cfg%have_walls = .false.
    cfg%have_merge = .false.
    cfg%xmin = 0.0_rk; cfg%xmax = 0.0_rk; cfg%ymin = 0.0_rk; cfg%ymax = 0.0_rk
    cfg%merge_radius = 0.0_rk
    allocate(cfg%burns(0))
    allocate(cfg%mass_changes(0))
    allocate(cfg%tidal_points(0))
    allocate(cfg%drag_zones(0))

    open(newunit=unit, file=trim(path), status='old', action='read')
    read(unit, *) key, cfg%nsteps
    read(unit, *) key, cfg%dt
    read(unit, *) key, cfg%g
    read(unit, *) key, cfg%output_every

    do
       read(unit, '(A)', iostat=ios) line
       if (ios /= 0) stop 11
       read(line, *) key
       select case (trim(key))
       case ('epoch')
          read(line, *) key, cfg%epoch_step, cfg%epoch_time
       case ('substeps')
          read(line, *) key, cfg%substeps
       case ('recenter_every')
          read(line, *) key, cfg%recenter_every
        case ('tidal_points')
           read(line, *) key, n
           if (allocated(cfg%tidal_points)) deallocate(cfg%tidal_points)
           allocate(cfg%tidal_points(n))
           do i = 1, n
              read(unit, *) cfg%tidal_points(i)%x, cfg%tidal_points(i)%y, cfg%tidal_points(i)%mass
           end do
         case ('elastic_walls')
            read(line, *) key, cfg%xmin, cfg%xmax, cfg%ymin, cfg%ymax
            cfg%have_walls = .true.
          case ('merge_radius')
             read(line, *) key, cfg%merge_radius
             cfg%have_merge = .true.
          case ('drag_zones')
             read(line, *) key, n
             if (allocated(cfg%drag_zones)) deallocate(cfg%drag_zones)
             allocate(cfg%drag_zones(n))
             do i = 1, n
                read(unit, *) cfg%drag_zones(i)%xmin, cfg%drag_zones(i)%xmax, &
                   cfg%drag_zones(i)%ymin, cfg%drag_zones(i)%ymax, &
                   cfg%drag_zones(i)%gamma
             end do
          case ('bodies')
             exit
       case default
          stop 12
       end select
    end do

    read(line, *) key, n
    allocate(cfg%bodies(n))
    do i = 1, n
       read(unit, *) cfg%bodies(i)%mass, cfg%bodies(i)%x, cfg%bodies(i)%y, cfg%bodies(i)%vx, cfg%bodies(i)%vy
    end do

    do
       read(unit, '(A)', iostat=ios) line
       if (ios /= 0) exit
       read(line, *) key, n
       select case (trim(key))
       case ('mass_changes')
          if (allocated(cfg%mass_changes)) deallocate(cfg%mass_changes)
          allocate(cfg%mass_changes(n))
          do i = 1, n
             read(unit, *) cfg%mass_changes(i)%step, cfg%mass_changes(i)%body, cfg%mass_changes(i)%factor
          end do
       case ('burns')
          if (allocated(cfg%burns)) deallocate(cfg%burns)
          allocate(cfg%burns(n))
          do i = 1, n
             read(unit, *) cfg%burns(i)%step, cfg%burns(i)%body, cfg%burns(i)%dvx, cfg%burns(i)%dvy
          end do
       case default
          stop 14
       end select
    end do
    close(unit)
  end subroutine read_case
end module config_mod
F90

cat > orbitsim/src/forces.f90 <<'F90'
module forces_mod
  use precision_mod
  use model_mod
  implicit none
contains
  subroutine accelerations(bodies, tides, g, ax, ay)
    type(body_state), intent(in) :: bodies(:)
    type(tidal_point), intent(in) :: tides(:)
    real(rk), intent(in) :: g
    real(rk), intent(out) :: ax(size(bodies)), ay(size(bodies))
    integer :: i, j
    real(rk) :: dx, dy, r2, invr3
    ax = 0.0_rk
    ay = 0.0_rk
    do i = 1, size(bodies)
       do j = 1, size(bodies)
          if (i /= j) then
             dx = bodies(j)%x - bodies(i)%x
             dy = bodies(j)%y - bodies(i)%y
             r2 = dx * dx + dy * dy
             invr3 = 1.0_rk / (r2 * sqrt(r2))
             ax(i) = ax(i) + g * bodies(j)%mass * dx * invr3
             ay(i) = ay(i) + g * bodies(j)%mass * dy * invr3
          end if
       end do
       do j = 1, size(tides)
          dx = tides(j)%x - bodies(i)%x
          dy = tides(j)%y - bodies(i)%y
          r2 = dx * dx + dy * dy
          invr3 = 1.0_rk / (r2 * sqrt(r2))
          ax(i) = ax(i) + g * tides(j)%mass * dx * invr3
          ay(i) = ay(i) + g * tides(j)%mass * dy * invr3
       end do
    end do
  end subroutine accelerations
end module forces_mod
F90

cat > orbitsim/src/integrator.f90 <<'F90'
module integrator_mod
  use precision_mod
  use model_mod
  use forces_mod
  implicit none
contains
  subroutine advance(bodies, tides, drag_zones, g, dt, substeps)
    type(body_state), intent(inout) :: bodies(:)
    type(tidal_point), intent(in) :: tides(:)
    type(drag_zone), intent(in) :: drag_zones(:)
    real(rk), intent(in) :: g, dt
    integer, intent(in) :: substeps
    real(rk), allocatable :: ax(:), ay(:)
    real(rk) :: h
    integer :: i, k, n
    n = size(bodies)
    h = dt / real(substeps, rk)
    allocate(ax(n), ay(n))
    do k = 1, substeps
       call accelerations(bodies, tides, g, ax, ay)
       do i = 1, n
          bodies(i)%vx = bodies(i)%vx + 0.5_rk * h * ax(i)
          bodies(i)%vy = bodies(i)%vy + 0.5_rk * h * ay(i)
          bodies(i)%x = bodies(i)%x + h * bodies(i)%vx
          bodies(i)%y = bodies(i)%y + h * bodies(i)%vy
       end do
       call accelerations(bodies, tides, g, ax, ay)
        do i = 1, n
           bodies(i)%vx = bodies(i)%vx + 0.5_rk * h * ax(i)
           bodies(i)%vy = bodies(i)%vy + 0.5_rk * h * ay(i)
        end do
        call apply_drag(bodies, drag_zones, h)
     end do
    deallocate(ax, ay)
  end subroutine advance
  subroutine apply_drag(bodies, drag_zones, h)
    type(body_state), intent(inout) :: bodies(:)
    type(drag_zone), intent(in) :: drag_zones(:)
    real(rk), intent(in) :: h
    integer :: i, j
    real(rk) :: scale
    do i = 1, size(bodies)
       do j = 1, size(drag_zones)
          if (bodies(i)%x >= drag_zones(j)%xmin .and. bodies(i)%x <= drag_zones(j)%xmax .and. &
              bodies(i)%y >= drag_zones(j)%ymin .and. bodies(i)%y <= drag_zones(j)%ymax) then
             scale = exp(-drag_zones(j)%gamma * h)
             bodies(i)%vx = bodies(i)%vx * scale
             bodies(i)%vy = bodies(i)%vy * scale
          end if
       end do
    end do
  end subroutine apply_drag
end module integrator_mod
F90

cat > orbitsim/src/invariants.f90 <<'F90'
module invariants_mod
  use precision_mod
  use model_mod
  implicit none
contains
  subroutine compute_invariants(bodies, tides, g, energy, angular_momentum)
    type(body_state), intent(in) :: bodies(:)
    type(tidal_point), intent(in) :: tides(:)
    real(rk), intent(in) :: g
    real(rk), intent(out) :: energy, angular_momentum
    integer :: i, j
    real(rk) :: dx, dy, r
    energy = 0.0_rk
    angular_momentum = 0.0_rk
    do i = 1, size(bodies)
       energy = energy + 0.5_rk * bodies(i)%mass * (bodies(i)%vx * bodies(i)%vx + bodies(i)%vy * bodies(i)%vy)
       angular_momentum = angular_momentum + bodies(i)%mass * (bodies(i)%x * bodies(i)%vy - bodies(i)%y * bodies(i)%vx)
    end do
    do i = 1, size(bodies) - 1
       do j = i + 1, size(bodies)
          dx = bodies(i)%x - bodies(j)%x
          dy = bodies(i)%y - bodies(j)%y
          r = sqrt(dx * dx + dy * dy)
          energy = energy - g * bodies(i)%mass * bodies(j)%mass / r
       end do
    end do
    do i = 1, size(bodies)
       do j = 1, size(tides)
          dx = bodies(i)%x - tides(j)%x
          dy = bodies(i)%y - tides(j)%y
          r = sqrt(dx * dx + dy * dy)
          energy = energy - g * bodies(i)%mass * tides(j)%mass / r
       end do
    end do
  end subroutine compute_invariants
end module invariants_mod
F90

cat > orbitsim/src/output.f90 <<'F90'
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
  subroutine record_outputs(traj_unit, inv_unit, step, time, bodies, tides, g)
    integer, intent(in) :: traj_unit, inv_unit, step
    real(rk), intent(in) :: time, g
    type(body_state), intent(in) :: bodies(:)
    type(tidal_point), intent(in) :: tides(:)
    integer :: i
    real(rk) :: energy, angular_momentum
    do i = 1, size(bodies)
       write(traj_unit, '(I0,1X,ES24.16,1X,I0,5(1X,ES24.16))') &
          step, time, i, bodies(i)%mass, bodies(i)%x, bodies(i)%y, &
          bodies(i)%vx, bodies(i)%vy
    end do
    call compute_invariants(bodies, tides, g, energy, angular_momentum)
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
F90

cat > orbitsim/src/checkpoint.f90 <<'F90'
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
    write(unit, '(I0,1X,ES24.16,1X,I0)') step, time, size(bodies)
    do i = 1, size(bodies)
       write(unit, '(5(1X,ES24.16))') bodies(i)%mass, bodies(i)%x, bodies(i)%y, bodies(i)%vx, bodies(i)%vy
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
F90

cat > orbitsim/src/main.f90 <<'F90'
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
     step = cfg%epoch_step
     time = cfg%epoch_time
  end if

  call open_outputs(trim(outdir), traj_unit, inv_unit)
  call record_outputs(traj_unit, inv_unit, step, time, bodies, cfg%tidal_points, cfg%g)
  if (have_checkpoint) then
     do while (step < checkpoint_at)
        call do_step()
     end do
     checkpoint_path = trim(outdir)//'/checkpoint.dat'
     call write_checkpoint(trim(checkpoint_path), step, time, bodies)
     if (mod(step, cfg%output_every) /= 0) call record_outputs(traj_unit, inv_unit, step, time, bodies, cfg%tidal_points, cfg%g)
     call write_final(trim(outdir), bodies)
     close(traj_unit); close(inv_unit)
     stop
  end if
  do while (step < cfg%nsteps)
     call do_step()
  end do
  call write_final(trim(outdir), bodies)
  close(traj_unit); close(inv_unit)
contains
  subroutine do_step()
    call apply_mass_changes(bodies, cfg%mass_changes, step + 1)
    call apply_burns(bodies, cfg%burns, step + 1)
    call advance(bodies, cfg%tidal_points, cfg%drag_zones, cfg%g, cfg%dt, cfg%substeps)
    step = step + 1
    time = time + cfg%dt
    if (cfg%have_walls) call reflect_walls(bodies, cfg%xmin, cfg%xmax, cfg%ymin, cfg%ymax)
    if (cfg%have_merge) call merge_bodies(bodies, cfg%merge_radius)
    if (cfg%recenter_every > 0 .and. mod(step, cfg%recenter_every) == 0) call recenter_bodies(bodies)
    if (mod(step, cfg%output_every) == 0 .or. step == cfg%nsteps) then
       call record_outputs(traj_unit, inv_unit, step, time, bodies, &
          cfg%tidal_points, cfg%g)
    end if
  end subroutine do_step
  subroutine apply_mass_changes(bodies, mass_changes, target_step)
    type(body_state), intent(inout) :: bodies(:)
    type(mass_event), intent(in) :: mass_changes(:)
    integer, intent(in) :: target_step
    integer :: i, b
    do i = 1, size(mass_changes)
       if (mass_changes(i)%step == target_step) then
          b = mass_changes(i)%body
          if (b >= 1 .and. b <= size(bodies)) bodies(b)%mass = bodies(b)%mass * mass_changes(i)%factor
       end if
    end do
  end subroutine apply_mass_changes
  subroutine apply_burns(bodies, burns, target_step)
    type(body_state), intent(inout) :: bodies(:)
    type(burn_event), intent(in) :: burns(:)
    integer, intent(in) :: target_step
    integer :: i, b
    do i = 1, size(burns)
       if (burns(i)%step == target_step) then
          b = burns(i)%body
          if (b >= 1 .and. b <= size(bodies)) then
             bodies(b)%vx = bodies(b)%vx + burns(i)%dvx
             bodies(b)%vy = bodies(b)%vy + burns(i)%dvy
          end if
       end if
    end do
  end subroutine apply_burns
  subroutine reflect_walls(bodies, xmin, xmax, ymin, ymax)
    type(body_state), intent(inout) :: bodies(:)
    real(rk), intent(in) :: xmin, xmax, ymin, ymax
    integer :: i
    do i = 1, size(bodies)
       do while (bodies(i)%x < xmin .or. bodies(i)%x > xmax)
          if (bodies(i)%x < xmin) then
             bodies(i)%x = 2.0_rk * xmin - bodies(i)%x
          else
             bodies(i)%x = 2.0_rk * xmax - bodies(i)%x
          end if
          bodies(i)%vx = -bodies(i)%vx
       end do
       do while (bodies(i)%y < ymin .or. bodies(i)%y > ymax)
          if (bodies(i)%y < ymin) then
             bodies(i)%y = 2.0_rk * ymin - bodies(i)%y
          else
             bodies(i)%y = 2.0_rk * ymax - bodies(i)%y
          end if
          bodies(i)%vy = -bodies(i)%vy
       end do
    end do
  end subroutine reflect_walls
  subroutine merge_bodies(bodies, radius)
    type(body_state), allocatable, intent(inout) :: bodies(:)
    real(rk), intent(in) :: radius
    type(body_state), allocatable :: merged(:)
    integer :: i, j, k, n
    real(rk) :: dx, dy, mt
    logical :: changed
    changed = .true.
    do while (changed)
       changed = .false.
       n = size(bodies)
       do i = 1, n
          do j = i + 1, n
             dx = bodies(i)%x - bodies(j)%x
             dy = bodies(i)%y - bodies(j)%y
             if (sqrt(dx * dx + dy * dy) <= radius) then
                mt = bodies(i)%mass + bodies(j)%mass
                bodies(i)%x = (bodies(i)%mass * bodies(i)%x + bodies(j)%mass * bodies(j)%x) / mt
                bodies(i)%y = (bodies(i)%mass * bodies(i)%y + bodies(j)%mass * bodies(j)%y) / mt
                bodies(i)%vx = (bodies(i)%mass * bodies(i)%vx + bodies(j)%mass * bodies(j)%vx) / mt
                bodies(i)%vy = (bodies(i)%mass * bodies(i)%vy + bodies(j)%mass * bodies(j)%vy) / mt
                bodies(i)%mass = mt
                allocate(merged(n - 1))
                k = 0
                do n = 1, size(bodies)
                   if (n /= j) then
                      k = k + 1
                      merged(k) = bodies(n)
                   end if
                end do
                call move_alloc(merged, bodies)
                changed = .true.
                exit
             end if
          end do
          if (changed) exit
       end do
    end do
  end subroutine merge_bodies
  subroutine recenter_bodies(bodies)
    type(body_state), intent(inout) :: bodies(:)
    integer :: i
    real(rk) :: total_mass, cx, cy, cvx, cvy
    total_mass = 0.0_rk; cx = 0.0_rk; cy = 0.0_rk; cvx = 0.0_rk; cvy = 0.0_rk
    do i = 1, size(bodies)
       total_mass = total_mass + bodies(i)%mass
       cx = cx + bodies(i)%mass * bodies(i)%x
       cy = cy + bodies(i)%mass * bodies(i)%y
       cvx = cvx + bodies(i)%mass * bodies(i)%vx
       cvy = cvy + bodies(i)%mass * bodies(i)%vy
    end do
    cx = cx / total_mass; cy = cy / total_mass; cvx = cvx / total_mass; cvy = cvy / total_mass
    do i = 1, size(bodies)
       bodies(i)%x = bodies(i)%x - cx
       bodies(i)%y = bodies(i)%y - cy
       bodies(i)%vx = bodies(i)%vx - cvx
       bodies(i)%vy = bodies(i)%vy - cvy
    end do
  end subroutine recenter_bodies
end program orbitsim
F90

make -C orbitsim clean
make -C orbitsim

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
cat > "$tmpdir/smoke.case" <<'CASE'
nsteps 12
dt 0.01
g 0.8
output_every 4
substeps 3
tidal_points 1
1.8 0.4 0.2
bodies 2
1 -0.5 0 0 -0.5
1 0.5 0 0 0.5
burns 1
7 2 0.001 -0.002
CASE
orbitsim/orbitsim "$tmpdir/smoke.case" "$tmpdir/out"
test -s "$tmpdir/out/invariants.tsv"
