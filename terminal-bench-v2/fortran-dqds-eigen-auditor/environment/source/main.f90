module audit_kinds
  implicit none
  integer, parameter :: dp = selected_real_kind(15, 300)
end module audit_kinds

module audit_config
  use audit_kinds
  implicit none
  private
  public :: case_config, read_config, load_checkpoint, save_checkpoint

  type :: case_config
     integer :: n = 0
     real(dp) :: scale = 1.0_dp
     real(dp), allocatable :: diag(:)
     real(dp), allocatable :: offdiag(:)
     logical :: restart = .false.
  end type case_config

contains

  subroutine read_config(path, cfg)
    character(len=*), intent(in) :: path
    type(case_config), intent(out) :: cfg
    character(len=4096) :: line, key, val
    integer :: u, ios, eq
    open(newunit=u, file=trim(path), status='old', action='read', iostat=ios)
    if (ios /= 0) stop 2
    do
       read(u, '(A)', iostat=ios) line
       if (ios /= 0) exit
       line = adjustl(line)
       if (len_trim(line) == 0 .or. line(1:1) == '#') cycle
       eq = index(line, '=')
       if (eq <= 0) cycle
       key = adjustl(line(:eq-1))
       val = adjustl(line(eq+1:))
       select case (trim(key))
       case ('n')
          read(val, *) cfg%n
          if (allocated(cfg%diag)) deallocate(cfg%diag)
          if (allocated(cfg%offdiag)) deallocate(cfg%offdiag)
          allocate(cfg%diag(cfg%n), cfg%offdiag(max(0,cfg%n-1)))
          cfg%diag = 0.0_dp
          if (cfg%n > 1) cfg%offdiag = 0.0_dp
       case ('scale')
          read(val, *) cfg%scale
       case ('diag')
          call read_vector(val, cfg%diag)
       case ('offdiag')
          if (cfg%n > 1) call read_vector(val, cfg%offdiag)
       case ('restart')
          call parse_bool(val, cfg%restart)
       end select
    end do
    close(u)
    if (cfg%n <= 0 .or. .not. allocated(cfg%diag)) stop 3
  end subroutine read_config

  subroutine read_vector(text, vec)
    character(len=*), intent(in) :: text
    real(dp), intent(inout) :: vec(:)
    character(len=len(text)) :: tmp
    integer :: i
    tmp = text
    do i = 1, len(tmp)
       if (tmp(i:i) == ',') tmp(i:i) = ' '
    end do
    read(tmp, *) vec
  end subroutine read_vector

  subroutine parse_bool(text, value)
    character(len=*), intent(in) :: text
    logical, intent(out) :: value
    character(len=:), allocatable :: t
    t = adjustl(trim(text))
    value = .false.
    if (len(t) > 0) then
       select case (t(1:1))
       case ('t', 'T', '1', 'y', 'Y')
          value = .true.
       end select
    end if
  end subroutine parse_bool

  subroutine save_checkpoint(path, cfg)
    character(len=*), intent(in) :: path
    type(case_config), intent(in) :: cfg
    integer :: u
    open(newunit=u, file=trim(path), status='replace', action='write')
    write(u,*) cfg%n
    write(u,'(ES24.16E3)') cfg%scale
    write(u,'(*(ES24.16E3,1X))') cfg%diag
    if (cfg%n > 1) write(u,'(*(ES24.16E3,1X))') cfg%offdiag
    close(u)
  end subroutine save_checkpoint

  subroutine load_checkpoint(path, cfg)
    character(len=*), intent(in) :: path
    type(case_config), intent(out) :: cfg
    integer :: u, ios
    real(dp) :: ignored_scale
    open(newunit=u, file=trim(path), status='old', action='read', iostat=ios)
    if (ios /= 0) stop 4
    read(u,*) cfg%n
    allocate(cfg%diag(cfg%n), cfg%offdiag(max(0,cfg%n-1)))
    read(u,*) ignored_scale
    cfg%scale = 1.0_dp
    read(u,*) cfg%diag
    if (cfg%n > 1) read(u,*) cfg%offdiag
    cfg%restart = .true.
    close(u)
  end subroutine load_checkpoint
end module audit_config

module eigen_core
  use audit_kinds
  implicit none
  private
  public :: make_matrix, jacobi_eigen, sort_pairs, audit_values, block_count

contains
  subroutine make_matrix(n, diag, offdiag, scale, a)
    integer, intent(in) :: n
    real(dp), intent(in) :: diag(n), offdiag(max(0,n-1)), scale
    real(dp), intent(out) :: a(n,n)
    integer :: i
    a = 0.0_dp
    do i = 1, n
       a(i,i) = scale * diag(i)
    end do
    do i = 1, n-1
       if (abs(offdiag(i)) < 1.0e-12_dp) then
          a(i,i+1) = 0.0_dp
       else
          a(i,i+1) = scale * offdiag(i)
       end if
       a(i+1,i) = a(i,i+1)
    end do
  end subroutine make_matrix

  subroutine jacobi_eigen(n, a, values, vecs)
    integer, intent(in) :: n
    real(dp), intent(in) :: a(n,n)
    real(dp), intent(out) :: values(n), vecs(n,n)
    real(dp) :: b(n,n), maxv, theta, c, s, app, aqq, apq, bkp, bkq, scale_b
    integer :: i, j, p, q, k, iter
    b = a
    vecs = 0.0_dp
    do i = 1, n
       vecs(i,i) = 1.0_dp
    end do
    if (n == 1) then
       values(1) = b(1,1)
       return
    end if
    do iter = 1, 20000
       maxv = 0.0_dp; p = 1; q = 2
       do i = 1, n-1
          do j = i+1, n
             if (abs(b(i,j)) > maxv) then
                maxv = abs(b(i,j)); p = i; q = j
             end if
          end do
       end do
       scale_b = max(1.0_dp, maxval(abs(b)))
       if (maxv <= 1.0e-13_dp * scale_b) exit
       app = b(p,p); aqq = b(q,q); apq = b(p,q)
       theta = 0.5_dp * atan2(2.0_dp*apq, aqq-app)
       c = cos(theta); s = sin(theta)
       do k = 1, n
          if (k /= p .and. k /= q) then
             bkp = b(k,p); bkq = b(k,q)
             b(k,p) = c*bkp - s*bkq
             b(p,k) = b(k,p)
             b(k,q) = s*bkp + c*bkq
             b(q,k) = b(k,q)
          end if
       end do
       b(p,p) = c*c*app - 2.0_dp*s*c*apq + s*s*aqq
       b(q,q) = s*s*app + 2.0_dp*s*c*apq + c*c*aqq
       b(p,q) = 0.0_dp; b(q,p) = 0.0_dp
       do k = 1, n
          bkp = vecs(k,p); bkq = vecs(k,q)
          vecs(k,p) = c*bkp - s*bkq
          vecs(k,q) = s*bkp + c*bkq
       end do
    end do
    do i = 1, n
       values(i) = b(i,i)
    end do
  end subroutine jacobi_eigen

  subroutine sort_pairs(n, values, vecs)
    integer, intent(in) :: n
    real(dp), intent(inout) :: values(n), vecs(n,n)
    integer :: i, j
    real(dp) :: tmp
    do i = 1, n-1
       do j = i+1, n
          if (values(j) < values(i)) then
             tmp = values(i); values(i) = values(j); values(j) = tmp
          end if
       end do
    end do
  end subroutine sort_pairs

  integer function block_count(n, offdiag) result(count)
    integer, intent(in) :: n
    real(dp), intent(in) :: offdiag(max(0,n-1))
    integer :: i
    count = 1
    do i = 1, n-1
       if (abs(offdiag(i)) < 1.0e-12_dp) count = count + 1
    end do
  end function block_count

  subroutine audit_values(n, a, values, vecs, residual_max, orthogonality_max, trace_error)
    integer, intent(in) :: n
    real(dp), intent(in) :: a(n,n), values(n), vecs(n,n)
    real(dp), intent(out) :: residual_max, orthogonality_max, trace_error
    real(dp) :: norm_a, trace_a, r, dotv, tmp
    integer :: i, j, k
    norm_a = 0.0_dp
    do i = 1, n
       tmp = sum(abs(a(i,:)))
       if (tmp > norm_a) norm_a = tmp
    end do
    residual_max = 0.0_dp
    do i = 1, n
       r = 0.0_dp
       do j = 1, n
          tmp = 0.0_dp
          do k = 1, n
             tmp = tmp + a(j,k) * vecs(k,i)
          end do
          r = max(r, abs(tmp - values(i)*vecs(j,i)))
       end do
       residual_max = max(residual_max, r / (max(1.0_dp,norm_a) * max(1.0_dp,abs(values(i)))))
    end do
    orthogonality_max = 0.0_dp
    do i = 1, n
       do j = 1, n
          dotv = sum(vecs(:,i) * vecs(:,j))
          if (i == j) dotv = dotv - 1.0_dp
          orthogonality_max = max(orthogonality_max, abs(dotv))
       end do
    end do
    trace_a = 0.0_dp
    do i = 1, n
       trace_a = trace_a + a(i,i)
    end do
    trace_error = abs(sum(values) - trace_a) / max(1.0_dp, abs(trace_a))
  end subroutine audit_values
end module eigen_core

program eigenaudit
  use audit_kinds
  use audit_config
  use eigen_core
  implicit none
  type(case_config) :: cfg, disk_cfg
  character(len=512) :: config_path, report_path, checkpoint_path
  real(dp), allocatable :: a(:,:), values(:), vecs(:,:)
  real(dp) :: residual_max, orthogonality_max, trace_error
  integer :: argc

  argc = command_argument_count()
  if (argc /= 3) then
     write(*,*) 'usage: eigenaudit <case.in> <report.json> <checkpoint.bin>'
     stop 64
  end if
  call get_command_argument(1, config_path)
  call get_command_argument(2, report_path)
  call get_command_argument(3, checkpoint_path)
  call read_config(config_path, cfg)
  if (cfg%restart) then
     call load_checkpoint(checkpoint_path, disk_cfg)
     cfg = disk_cfg
  else
     call save_checkpoint(checkpoint_path, cfg)
  end if
  allocate(a(cfg%n,cfg%n), values(cfg%n), vecs(cfg%n,cfg%n))
  call make_matrix(cfg%n, cfg%diag, cfg%offdiag, cfg%scale, a)
  call jacobi_eigen(cfg%n, a, values, vecs)
  call sort_pairs(cfg%n, values, vecs)
  call audit_values(cfg%n, a, values, vecs, residual_max, orthogonality_max, trace_error)
  call write_report(report_path, cfg%n, cfg%scale, values, residual_max, orthogonality_max, trace_error, block_count(cfg%n, cfg%offdiag))
contains
  function number(x) result(s)
    real(dp), intent(in) :: x
    character(len=40) :: s
    write(s,'(ES24.16E3)') x
    s = adjustl(s)
  end function number

  subroutine write_report(path, n, scale, values, residual_max, orthogonality_max, trace_error, blocks)
    character(len=*), intent(in) :: path
    integer, intent(in) :: n, blocks
    real(dp), intent(in) :: scale, values(n), residual_max, orthogonality_max, trace_error
    integer :: u, i
    open(newunit=u, file=trim(path), status='replace', action='write')
    write(u,'(A,I0,A)') '{"n":', n, ','
    write(u,'(A,A,A)') '"scale":', trim(number(scale)), ','
    write(u,'(A)', advance='no') '"eigenvalues":['
    do i = 1, n
       if (i > 1) write(u,'(A)', advance='no') ','
       write(u,'(A)', advance='no') trim(number(values(i)))
    end do
    write(u,'(A)') '],'
    write(u,'(A,A,A)') '"residual_max":', trim(number(residual_max)), ','
    write(u,'(A,A,A)') '"orthogonality_max":', trim(number(orthogonality_max)), ','
    write(u,'(A,A,A)') '"trace_error":', trim(number(trace_error)), ','
    write(u,'(A,I0,A)') '"block_count":', blocks, '}'
    close(u)
  end subroutine write_report
end program eigenaudit
