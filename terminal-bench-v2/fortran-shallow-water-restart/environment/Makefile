FC = gfortran
FFLAGS ?= -std=f2018 -O2 -Wall -Wextra -fcheck=all

.PHONY: all run clean

all: bin/shallow_water

bin/shallow_water: src/shallow_water.f90
	mkdir -p bin
	$(FC) $(FFLAGS) -o $@ $<

run: bin/shallow_water
	mkdir -p out
	./bin/shallow_water --nx 48 --steps 72 --split 31 --seed 19 --prefix out/default

clean:
	rm -rf bin out *.mod restart_*.chk
