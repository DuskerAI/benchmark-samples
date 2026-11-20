DC=ldc2
DFLAGS=-O2 -release -boundscheck=on
SOURCES=source/main.d source/model.d source/linalg.d source/stateio.d

all: continuation

continuation: $(SOURCES)
	$(DC) $(DFLAGS) $(SOURCES) -of=$@

clean:
	rm -f continuation continuation.o source/*.o
