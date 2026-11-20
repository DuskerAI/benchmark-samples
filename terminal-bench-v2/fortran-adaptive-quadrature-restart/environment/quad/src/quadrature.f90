program quad_solver
    implicit none
    character(len=256) :: cfg_path, out_path, chk_path
    real(8) :: alpha, beta, omega, tol, result, err
    integer :: checkpoint_interval, panel_limit, panels, argc, rc
    logical :: restart

    argc = command_argument_count()
    if (argc /= 3) stop 2
    call get_command_argument(1, cfg_path)
    call get_command_argument(2, out_path)
    call get_command_argument(3, chk_path)

    call read_config(trim(cfg_path), alpha, beta, omega, tol, checkpoint_interval, panel_limit, restart, rc)
    if (rc /= 0) stop 2
    if (alpha <= -0.5d0 .or. beta <= -0.5d0 .or. omega < 0.d0) stop 2
    if (tol < 1.d-10 .or. tol > 1.d-5 .or. checkpoint_interval < 0 .or. panel_limit < 0) stop 2

    if (restart) call load_seed(trim(chk_path), result)
    call integrate(alpha, beta, omega, tol, result, err, panels)
    if (checkpoint_interval > 0) call write_seed(trim(chk_path), result)
    call write_output(trim(out_path), result, err, panels)
contains
    subroutine read_config(path, alpha, beta, omega, tol, checkpoint_interval, panel_limit, restart, rc)
        character(len=*), intent(in) :: path
        real(8), intent(out) :: alpha, beta, omega, tol
        integer, intent(out) :: checkpoint_interval, panel_limit, rc
        logical, intent(out) :: restart
        character(len=512) :: line, key, val
        integer :: unit, ios, p
        alpha = 0.d0; beta = 0.d0; omega = 0.d0; tol = 1.d-8
        checkpoint_interval = 0; panel_limit = 0; restart = .false.; rc = 0
        open(newunit=unit, file=path, status='old', action='read', iostat=ios)
        if (ios /= 0) then; rc = 1; return; end if
        do
            read(unit, '(A)', iostat=ios) line
            if (ios /= 0) exit
            p = index(line, '=')
            if (p <= 1) cycle
            key = adjustl(line(:p-1)); val = adjustl(line(p+1:))
            select case (trim(key))
            case ('alpha'); read(val, *, iostat=ios) alpha
            case ('beta'); read(val, *, iostat=ios) beta
            case ('omega'); read(val, *, iostat=ios) omega
            case ('tol'); read(val, *, iostat=ios) tol
            case ('checkpoint_interval'); read(val, *, iostat=ios) checkpoint_interval
            case ('panel_limit'); read(val, *, iostat=ios) panel_limit
            case ('restart'); restart = index(trim(val), 'true') > 0 .or. index(trim(val), 'T') > 0
            end select
            if (ios /= 0) rc = 1
        end do
        close(unit)
    end subroutine

    real(8) function f(x, alpha, beta, omega)
        real(8), intent(in) :: x, alpha, beta, omega
        f = x**alpha * (1.d0 - x)**beta * cos(omega*x)
    end function

    recursive subroutine adapt(a, b, fa, fm, fb, whole, eps, depth, alpha, beta, omega, result, err, panels)
        real(8), intent(in) :: a, b, fa, fm, fb, whole, eps, alpha, beta, omega
        integer, intent(in) :: depth
        real(8), intent(inout) :: result, err
        integer, intent(inout) :: panels
        real(8) :: m, lm, rm, flm, frm, left, right, delta
        m = 0.5d0*(a+b); lm = 0.5d0*(a+m); rm = 0.5d0*(m+b)
        flm = f(lm, alpha, beta, omega); frm = f(rm, alpha, beta, omega)
        left = (m-a)*(fa + 4.d0*flm + fm)/6.d0
        right = (b-m)*(fm + 4.d0*frm + fb)/6.d0
        delta = left + right - whole
        if (abs(delta) < 15.d0*eps .or. depth > 30) then
            result = result + left + right + delta/15.d0
            err = err + abs(delta)/15.d0
            panels = panels + 1
        else
            call adapt(a, m, fa, flm, fm, left, eps/2.d0, depth+1, alpha, beta, omega, result, err, panels)
            call adapt(m, b, fm, frm, fb, right, eps/2.d0, depth+1, alpha, beta, omega, result, err, panels)
        end if
    end subroutine

    subroutine integrate(alpha, beta, omega, tol, result, err, panels)
        real(8), intent(in) :: alpha, beta, omega, tol
        real(8), intent(out) :: result, err
        integer, intent(out) :: panels
        real(8) :: fa, fm, fb, whole
        fa = f(0.d0, alpha, beta, omega); fm = f(0.5d0, alpha, beta, omega); fb = f(1.d0, alpha, beta, omega)
        whole = (fa + 4.d0*fm + fb)/6.d0
        result = 0.d0; err = 0.d0; panels = 0
        call adapt(0.d0, 1.d0, fa, fm, fb, whole, tol, 0, alpha, beta, omega, result, err, panels)
    end subroutine

    subroutine write_output(path, result, err, panels)
        character(len=*), intent(in) :: path
        real(8), intent(in) :: result, err
        integer, intent(in) :: panels
        integer :: unit
        open(newunit=unit, file=path, status='replace', action='write')
        write(unit, '(A)') 'result,error_estimate,panels'
        write(unit, '(ES26.17,",",ES26.17,",",I0)') result, err, panels
        close(unit)
    end subroutine

    subroutine write_seed(path, result)
        character(len=*), intent(in) :: path
        real(8), intent(in) :: result
        integer :: unit
        open(newunit=unit, file=path, status='replace', action='write')
        write(unit, '(ES26.17)') result
        close(unit)
    end subroutine

    subroutine load_seed(path, result)
        character(len=*), intent(in) :: path
        real(8), intent(out) :: result
        integer :: unit, ios
        open(newunit=unit, file=path, status='old', action='read', iostat=ios)
        if (ios == 0) read(unit, *, iostat=ios) result
        if (ios == 0) close(unit)
    end subroutine
end program quad_solver
