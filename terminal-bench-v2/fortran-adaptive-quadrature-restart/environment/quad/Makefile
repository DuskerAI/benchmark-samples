FC = gfortran
FFLAGS = -O2 -std=f2008 -Wall -Wextra -ffree-line-length-none

all: quad_solver

quad_solver: src/quadrature.f90
	$(FC) $(FFLAGS) -o $@ $<

clean:
	rm -f quad_solver *.o *.mod
