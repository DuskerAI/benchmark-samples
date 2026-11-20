.PHONY: all clean

all: spectral

spectral: src/Main.hs
	ghc -O2 -Wall -o spectral src/Main.hs

clean:
	rm -f spectral src/*.hi src/*.o
