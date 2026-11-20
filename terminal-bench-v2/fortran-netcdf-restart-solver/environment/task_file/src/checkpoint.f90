module checkpoint_mod
  use iso_fortran_env, only: int32, int64, real64
  use config_mod, only: run_config
  implicit none

contains

  subroutine append_checkpoint(path, step, t, u)
    character(len=*), intent(in) :: path
    integer, intent(in) :: step
    real(real64), intent(in) :: t
    real(real64), intent(in) :: u(:)
    integer :: unit
    character(len=4) :: magic
    integer(int32) :: nbytes, step32, nx32
    if (len_trim(path) == 0) return
    magic = 'FNC1'
    step32 = int(step, int32)
    nx32 = int(size(u), int32)
    nbytes = int(12 + 8 * size(u), int32)
    open(newunit=unit, file=path, access='stream', form='unformatted', &
      status='unknown', position='append', action='write')
    write(unit) magic
    write(unit) nbytes
    write(unit) step32
    write(unit) nx32
    write(unit) t
    write(unit) u
    close(unit)
  end subroutine append_checkpoint

  subroutine load_checkpoint(path, target_steps, cfg, u, start_step, ok)
    character(len=*), intent(in) :: path
    integer, intent(in) :: target_steps
    type(run_config), intent(in) :: cfg
    real(real64), intent(out) :: u(:)
    integer, intent(out) :: start_step
    logical, intent(out) :: ok
    integer :: unit, ios
    integer(int64) :: pos, fsize
    integer(int32) :: nbytes, step32, nx32
    real(real64) :: t
    character(len=4) :: magic
    logical :: exists

    ok = .false.
    start_step = 0
    inquire(file=path, exist=exists, size=fsize)
    if (.not. exists .or. fsize < 8_int64) return
    open(newunit=unit, file=path, access='stream', form='unformatted', &
      status='old', action='read', iostat=ios)
    if (ios /= 0) return
    pos = 1_int64
    do while (pos + 7_int64 <= fsize)
      read(unit, pos=pos, iostat=ios) magic
      if (ios /= 0 .or. magic /= 'FNC1') exit
      read(unit, pos=pos + 4_int64, iostat=ios) nbytes
      if (ios /= 0 .or. nbytes < 16) exit
      if (pos + 7_int64 + int(nbytes, int64) > fsize) exit
      read(unit, pos=pos + 8_int64, iostat=ios) step32, nx32, t
      if (ios /= 0) exit
      if (nx32 == cfg%nx .and. step32 <= target_steps .and. .not. ok) then
        read(unit, pos=pos + 24_int64, iostat=ios) u
        if (ios == 0) then
          start_step = int(step32)
          ok = .true.
        end if
      end if
      pos = pos + 8_int64 + int(nbytes, int64)
    end do
    close(unit)
  end subroutine load_checkpoint

end module checkpoint_mod
