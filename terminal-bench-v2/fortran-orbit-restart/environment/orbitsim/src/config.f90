module config_mod
  use precision_mod
  use model_mod
  implicit none
contains
  subroutine read_case(path, cfg)
    character(len=*), intent(in) :: path
    type(case_config), intent(out) :: cfg
    character(len=64) :: key
    integer :: unit, n, i, ios
    character(len=256) :: line

    cfg%recenter_every = 0
    cfg%have_merge = .false.
    cfg%merge_radius = 0.0_rk
    allocate(cfg%mass_changes(0))
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
       case ('drag_zones')
          read(line, *) key, n
          deallocate(cfg%drag_zones)
          allocate(cfg%drag_zones(n))
          do i = 1, n
             read(unit, *) cfg%drag_zones(i)%xmin, cfg%drag_zones(i)%xmax, &
                cfg%drag_zones(i)%ymin, cfg%drag_zones(i)%ymax, &
                cfg%drag_zones(i)%gamma
          end do
       case ('bodies')
          exit
       case default
          exit
       end select
    end do
    read(line, *) key, n
    allocate(cfg%bodies(n))
    do i = 1, n
       read(unit, *) cfg%bodies(i)%mass, cfg%bodies(i)%x, cfg%bodies(i)%y, cfg%bodies(i)%vx, cfg%bodies(i)%vy
    end do
    read(unit, '(A)', iostat=ios) line
    if (ios == 0) then
       read(line, *) key, n
       if (trim(key) == 'mass_changes') then
          deallocate(cfg%mass_changes)
          allocate(cfg%mass_changes(n))
          do i = 1, n
             read(unit, *) cfg%mass_changes(i)%step, cfg%mass_changes(i)%body, cfg%mass_changes(i)%factor
          end do
       end if
    end if
    close(unit)
  end subroutine read_case
end module config_mod
