#!/bin/bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path
path = Path('/app/eigen/main.f90')
s = path.read_text()
s = s.replace("""    integer :: u, ios
    real(dp) :: ignored_scale
    open(newunit=u, file=trim(path), status='old', action='read', iostat=ios)
    if (ios /= 0) stop 4
    read(u,*) cfg%n
    allocate(cfg%diag(cfg%n), cfg%offdiag(max(0,cfg%n-1)))
    read(u,*) ignored_scale
    cfg%scale = 1.0_dp
    read(u,*) cfg%diag""", """    integer :: u, ios
    open(newunit=u, file=trim(path), status='old', action='read', iostat=ios)
    if (ios /= 0) stop 4
    read(u,*) cfg%n
    allocate(cfg%diag(cfg%n), cfg%offdiag(max(0,cfg%n-1)))
    read(u,*) cfg%scale
    read(u,*) cfg%diag""")
s = s.replace("""    do i = 1, n-1
       if (abs(offdiag(i)) < 1.0e-12_dp) then
          a(i,i+1) = 0.0_dp
       else
          a(i,i+1) = scale * offdiag(i)
       end if
       a(i+1,i) = a(i,i+1)
    end do""", """    do i = 1, n-1
       a(i,i+1) = scale * offdiag(i)
       a(i+1,i) = a(i,i+1)
    end do""")
s = s.replace("""    integer :: i, j
    real(dp) :: tmp
    do i = 1, n-1
       do j = i+1, n
          if (values(j) < values(i)) then
             tmp = values(i); values(i) = values(j); values(j) = tmp
          end if
       end do
    end do""", """    integer :: i, j
    real(dp) :: tmp, col(n)
    do i = 1, n-1
       do j = i+1, n
          if (values(j) < values(i)) then
             tmp = values(i); values(i) = values(j); values(j) = tmp
             col = vecs(:,i); vecs(:,i) = vecs(:,j); vecs(:,j) = col
          end if
       end do
    end do""")
s = s.replace("""  integer function block_count(n, offdiag) result(count)
    integer, intent(in) :: n
    real(dp), intent(in) :: offdiag(max(0,n-1))
    integer :: i
    count = 1
    do i = 1, n-1
       if (abs(offdiag(i)) < 1.0e-12_dp) count = count + 1
    end do
  end function block_count""", """  integer function block_count(n, offdiag, scale) result(count)
    integer, intent(in) :: n
    real(dp), intent(in) :: offdiag(max(0,n-1)), scale
    integer :: i
    count = 1
    do i = 1, n-1
       if (scale * offdiag(i) == 0.0_dp) count = count + 1
    end do
  end function block_count""")
s = s.replace("block_count(cfg%n, cfg%offdiag))", "block_count(cfg%n, cfg%offdiag, cfg%scale))")
s = s.replace("(ES24.16E3)", "(ES26.18E3)")
s = s.replace("character(len=40) :: s", "character(len=44) :: s")
s = s.replace("do iter = 1, 20000", "do iter = 1, 50000")
s = s.replace("1.0e-13_dp * scale_b", "1.0e-14_dp * scale_b")
path.write_text(s)
PY

make -C /app/eigen clean all
