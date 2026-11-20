FC = gfortran-12
FFLAGS = -O2 -std=f2008 -Wall -Wextra -ffree-line-length-none

all: swe_dg

swe_dg: swe_dg.f90
	$(FC) $(FFLAGS) -o swe_dg swe_dg.f90

clean:
	rm -f swe_dg *.mod
