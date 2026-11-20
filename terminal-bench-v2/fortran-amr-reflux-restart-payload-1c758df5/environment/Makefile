FC = gfortran
FFLAGS = -std=f2008 -O2 -Wall -Wextra -fcheck=all -ffree-line-length-none

all: amr_solver

amr_solver: src/amr_solver.f90
	$(FC) $(FFLAGS) -o $@ $<

clean:
	rm -f amr_solver *.mod
