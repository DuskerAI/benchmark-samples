FC = gfortran
FFLAGS ?= -O2 -Wall -Wextra -std=f2008

SRC = src/precision.f90 src/model.f90 src/config.f90 src/forces.f90 src/integrator.f90 src/invariants.f90 src/checkpoint.f90 src/output.f90 src/main.f90
OBJ = $(SRC:.f90=.o)

all: orbitsim

orbitsim: $(OBJ)
	$(FC) $(FFLAGS) -o $@ $(OBJ)

%.o: %.f90
	$(FC) $(FFLAGS) -c $< -Jsrc -o $@

clean:
	rm -f src/*.o src/*.mod orbitsim
.PHONY: all clean
