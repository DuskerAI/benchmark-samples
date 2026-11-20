DC=ldc2
DFLAGS=-O2 -release

all: taylor_ode

taylor_ode: src/main.d src/config.d src/interval.d src/solver.d
	$(DC) $(DFLAGS) -of=$@ src/main.d src/config.d src/interval.d src/solver.d

clean:
	rm -f taylor_ode *.o
