#!/bin/bash
set -euo pipefail

cat > /app/quad/src/quadrature.f90 <<'F90'
program quad_solver
    implicit none
    integer, parameter :: dp = selected_real_kind(15, 300)
    integer, parameter :: max_stack = 200000
    real(dp), parameter :: pi = 3.141592653589793238462643383279502884_dp

    type config_t
        real(dp) :: alpha = 0.0_dp, beta = 0.0_dp, omega = 0.0_dp, tol = 1.0e-8_dp
        integer :: checkpoint_interval = 0, panel_limit = 0
        logical :: restart = .false.
    end type

    type interval_t
        real(dp) :: a, b, fa, fm, fb, s
        integer :: depth
    end type

    character(len=256) :: cfg_path, out_path, chk_path
    type(config_t) :: cfg
    type(interval_t), allocatable :: stack(:)
    real(dp) :: total, total_c, err, err_c
    integer :: argc, rc, nstack, accepted, accepted_start

    argc = command_argument_count()
    if (argc /= 3) stop 2
    call get_command_argument(1, cfg_path)
    call get_command_argument(2, out_path)
    call get_command_argument(3, chk_path)
    call read_config(trim(cfg_path), cfg, rc)
    if (rc /= 0 .or. .not. valid_config(cfg)) stop 2

    allocate(stack(max_stack))
    total = 0.0_dp; total_c = 0.0_dp; err = 0.0_dp; err_c = 0.0_dp
    accepted = 0; nstack = 0
    if (cfg%restart) then
        call read_checkpoint(trim(chk_path), cfg, stack, nstack, total, total_c, err, err_c, accepted, rc)
        if (rc /= 0) stop 2
    else
        call initialize_stack(cfg, stack, nstack)
    end if
    accepted_start = accepted
    call advance(cfg, trim(chk_path), stack, nstack, total, total_c, err, err_c, accepted, accepted_start)
    call write_output(trim(out_path), total, err, accepted)
    call write_checkpoint(trim(chk_path), cfg, stack, nstack, total, total_c, err, err_c, accepted)
contains
    logical function valid_config(cfg)
        type(config_t), intent(in) :: cfg
        valid_config = cfg%alpha > -0.5_dp .and. cfg%beta > -0.5_dp .and. cfg%omega >= 0.0_dp &
            .and. cfg%tol >= 1.0e-10_dp .and. cfg%tol <= 1.0e-5_dp &
            .and. cfg%checkpoint_interval >= 0 .and. cfg%panel_limit >= 0
    end function

    subroutine read_config(path, cfg, rc)
        character(len=*), intent(in) :: path
        type(config_t), intent(inout) :: cfg
        integer, intent(out) :: rc
        character(len=512) :: line, key, val
        integer :: unit, ios, p
        logical :: seen_alpha, seen_beta, seen_omega, seen_tol, seen_ci, seen_pl, seen_restart
        seen_alpha = .false.; seen_beta = .false.; seen_omega = .false.; seen_tol = .false.
        seen_ci = .false.; seen_pl = .false.; seen_restart = .false.; rc = 0
        open(newunit=unit, file=path, status='old', action='read', iostat=ios)
        if (ios /= 0) then; rc = 1; return; end if
        do
            read(unit, '(A)', iostat=ios) line
            if (ios /= 0) exit
            if (len_trim(line) == 0) cycle
            p = index(line, '=')
            if (p <= 1) then; rc = 1; exit; end if
            key = adjustl(line(:p-1)); val = adjustl(line(p+1:))
            select case (trim(key))
            case ('alpha')
                read(val, *, iostat=ios) cfg%alpha; seen_alpha = ios == 0
            case ('beta')
                read(val, *, iostat=ios) cfg%beta; seen_beta = ios == 0
            case ('omega')
                read(val, *, iostat=ios) cfg%omega; seen_omega = ios == 0
            case ('tol')
                read(val, *, iostat=ios) cfg%tol; seen_tol = ios == 0
            case ('checkpoint_interval')
                read(val, *, iostat=ios) cfg%checkpoint_interval; seen_ci = ios == 0
            case ('panel_limit')
                read(val, *, iostat=ios) cfg%panel_limit; seen_pl = ios == 0
            case ('restart')
                seen_restart = .true.; cfg%restart = trim(val) == 'true' .or. trim(val) == 'TRUE' .or. trim(val) == 'T'
            case default
                rc = 1; exit
            end select
            if (ios /= 0) then; rc = 1; exit; end if
        end do
        close(unit)
        if (.not. (seen_alpha .and. seen_beta .and. seen_omega .and. seen_tol .and. seen_ci .and. seen_pl .and. seen_restart)) rc = 1
    end subroutine

    real(dp) function phi(t, cfg)
        real(dp), intent(in) :: t
        type(config_t), intent(in) :: cfg
        real(dp) :: x, dxdt, s
        if (t <= 0.0_dp .or. t >= 1.0_dp) then
            phi = 0.0_dp
        else
            s = sin(0.5_dp*pi*t)
            x = s*s
            dxdt = 0.5_dp*pi*sin(pi*t)
            if (x <= 0.0_dp .or. x >= 1.0_dp) then
                phi = 0.0_dp
            else
                phi = (x**cfg%alpha) * ((1.0_dp - x)**cfg%beta) * cos(cfg%omega*x) * dxdt
            end if
        end if
    end function

    real(dp) function simp(a, b, fa, fm, fb)
        real(dp), intent(in) :: a, b, fa, fm, fb
        simp = (b - a) * (fa + 4.0_dp*fm + fb) / 6.0_dp
    end function

    subroutine initialize_stack(cfg, stack, nstack)
        type(config_t), intent(in) :: cfg
        type(interval_t), intent(inout) :: stack(:)
        integer, intent(out) :: nstack
        real(dp) :: fa, fm, fb
        fa = phi(0.0_dp, cfg); fm = phi(0.5_dp, cfg); fb = phi(1.0_dp, cfg)
        nstack = 1
        stack(1) = interval_t(0.0_dp, 1.0_dp, fa, fm, fb, simp(0.0_dp, 1.0_dp, fa, fm, fb), 0)
    end subroutine

    subroutine push(stack, nstack, item)
        type(interval_t), intent(inout) :: stack(:)
        integer, intent(inout) :: nstack
        type(interval_t), intent(in) :: item
        if (nstack >= size(stack)) stop 4
        nstack = nstack + 1
        stack(nstack) = item
    end subroutine

    subroutine add_kahan(total, comp, value)
        real(dp), intent(inout) :: total, comp
        real(dp), intent(in) :: value
        real(dp) :: y, t
        y = value - comp
        t = total + y
        comp = (t - total) - y
        total = t
    end subroutine

    subroutine advance(cfg, chk_path, stack, nstack, total, total_c, err, err_c, accepted, accepted_start)
        type(config_t), intent(in) :: cfg
        character(len=*), intent(in) :: chk_path
        type(interval_t), intent(inout) :: stack(:)
        integer, intent(inout) :: nstack, accepted
        integer, intent(in) :: accepted_start
        real(dp), intent(inout) :: total, total_c, err, err_c
        type(interval_t) :: iv, left_iv, right_iv
        real(dp) :: m, lm, rm, flm, frm, left, right, delta, value, evalue, local_tol
        do while (nstack > 0)
            iv = stack(nstack); nstack = nstack - 1
            m = 0.5_dp*(iv%a + iv%b); lm = 0.5_dp*(iv%a + m); rm = 0.5_dp*(m + iv%b)
            flm = phi(lm, cfg); frm = phi(rm, cfg)
            left = simp(iv%a, m, iv%fa, flm, iv%fm)
            right = simp(m, iv%b, iv%fm, frm, iv%fb)
            delta = left + right - iv%s
            local_tol = cfg%tol * (iv%b - iv%a)
            if (abs(delta) <= 15.0_dp*local_tol .or. iv%depth >= 64 .or. (iv%b - iv%a) < 1.0e-14_dp) then
                value = left + right + delta/15.0_dp
                evalue = abs(delta)/15.0_dp
                call add_kahan(total, total_c, value)
                call add_kahan(err, err_c, evalue)
                accepted = accepted + 1
                if (cfg%checkpoint_interval > 0) then
                    if (mod(accepted, cfg%checkpoint_interval) == 0) call write_checkpoint(chk_path, cfg, stack, nstack, total, total_c, err, err_c, accepted)
                end if
                if (cfg%panel_limit > 0 .and. accepted - accepted_start >= cfg%panel_limit) then
                    call write_checkpoint(chk_path, cfg, stack, nstack, total, total_c, err, err_c, accepted)
                    stop 3
                end if
            else
                left_iv = interval_t(iv%a, m, iv%fa, flm, iv%fm, left, iv%depth + 1)
                right_iv = interval_t(m, iv%b, iv%fm, frm, iv%fb, right, iv%depth + 1)
                call push(stack, nstack, right_iv)
                call push(stack, nstack, left_iv)
            end if
        end do
    end subroutine

    subroutine write_output(path, total, err, accepted)
        character(len=*), intent(in) :: path
        real(dp), intent(in) :: total, err
        integer, intent(in) :: accepted
        integer :: unit
        open(newunit=unit, file=path, status='replace', action='write')
        write(unit, '(A)') 'result,error_estimate,panels'
        write(unit, '(ES26.17,",",ES26.17,",",I0)') total, err, accepted
        close(unit)
    end subroutine

    subroutine write_checkpoint(path, cfg, stack, nstack, total, total_c, err, err_c, accepted)
        character(len=*), intent(in) :: path
        type(config_t), intent(in) :: cfg
        type(interval_t), intent(in) :: stack(:)
        integer, intent(in) :: nstack, accepted
        real(dp), intent(in) :: total, total_c, err, err_c
        integer :: unit, i
        open(newunit=unit, file=path, status='replace', action='write')
        write(unit, '(A)') 'quad-checkpoint-v1'
        write(unit, '(4(ES26.17,1X),3(I0,1X))') cfg%alpha, cfg%beta, cfg%omega, cfg%tol, cfg%checkpoint_interval, accepted, nstack
        write(unit, '(4(ES26.17,1X))') total, total_c, err, err_c
        do i = 1, nstack
            write(unit, '(6(ES26.17,1X),I0)') stack(i)%a, stack(i)%b, stack(i)%fa, stack(i)%fm, stack(i)%fb, stack(i)%s, stack(i)%depth
        end do
        close(unit)
    end subroutine

    subroutine read_checkpoint(path, cfg, stack, nstack, total, total_c, err, err_c, accepted, rc)
        character(len=*), intent(in) :: path
        type(config_t), intent(in) :: cfg
        type(interval_t), intent(inout) :: stack(:)
        integer, intent(out) :: nstack, accepted, rc
        real(dp), intent(out) :: total, total_c, err, err_c
        character(len=64) :: magic
        real(dp) :: alpha, beta, omega, tol
        integer :: unit, ios, i, ci
        rc = 0
        open(newunit=unit, file=path, status='old', action='read', iostat=ios)
        if (ios /= 0) then; rc = 1; return; end if
        read(unit, '(A)', iostat=ios) magic
        if (ios /= 0 .or. trim(magic) /= 'quad-checkpoint-v1') then; rc = 1; close(unit); return; end if
        read(unit, *, iostat=ios) alpha, beta, omega, tol, ci, accepted, nstack
        if (ios /= 0 .or. nstack < 0 .or. nstack > size(stack)) then; rc = 1; close(unit); return; end if
        if (abs(alpha-cfg%alpha) > 1.0e-20_dp .or. abs(beta-cfg%beta) > 1.0e-20_dp .or. abs(omega-cfg%omega) > 1.0e-20_dp .or. abs(tol-cfg%tol) > 1.0e-20_dp .or. ci /= cfg%checkpoint_interval) then
            rc = 1; close(unit); return
        end if
        read(unit, *, iostat=ios) total, total_c, err, err_c
        if (ios /= 0) then; rc = 1; close(unit); return; end if
        do i = 1, nstack
            read(unit, *, iostat=ios) stack(i)%a, stack(i)%b, stack(i)%fa, stack(i)%fm, stack(i)%fb, stack(i)%s, stack(i)%depth
            if (ios /= 0) then; rc = 1; exit; end if
        end do
        close(unit)
    end subroutine
end program quad_solver
F90

make -C /app/quad clean
make -C /app/quad
