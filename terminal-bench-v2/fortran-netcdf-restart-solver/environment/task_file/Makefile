FC := gfortran
FFLAGS := -std=f2008 -O2 -Wall -Wextra

SRC := src/config.f90 src/model.f90 src/checkpoint.f90 src/main.f90

all: restart_solver

restart_solver: $(SRC)
	$(FC) $(FFLAGS) $(SRC) -o $@

clean:
	rm -f restart_solver *.mod

.PHONY: all clean
