FC = gfortran
FFLAGS ?= -O2 -std=f2008 -ffree-line-length-none

all: eigenaudit

eigenaudit: main.f90
	$(FC) $(FFLAGS) -o $@ main.f90

clean:
	rm -f eigenaudit *.mod *.o
