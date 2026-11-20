module model_mod
  use precision_mod
  implicit none

  type :: body_state
     real(rk) :: mass
     real(rk) :: x
     real(rk) :: y
     real(rk) :: vx
     real(rk) :: vy
  end type body_state

  type :: mass_event
     integer :: step
     integer :: body
     real(rk) :: factor
  end type mass_event

  type :: drag_zone
     real(rk) :: xmin
     real(rk) :: xmax
     real(rk) :: ymin
     real(rk) :: ymax
     real(rk) :: gamma
  end type drag_zone

  type :: case_config
     integer :: nsteps
     integer :: output_every
     integer :: recenter_every
     real(rk) :: dt
     real(rk) :: g
     logical :: have_merge
     real(rk) :: merge_radius
     type(body_state), allocatable :: bodies(:)
     type(mass_event), allocatable :: mass_changes(:)
     type(drag_zone), allocatable :: drag_zones(:)
  end type case_config
end module model_mod
