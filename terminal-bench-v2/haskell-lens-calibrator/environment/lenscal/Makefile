GHC ?= ghc
HCFLAGS ?= -O2 -Wall

.PHONY: all clean

all: lenscal

lenscal: Main.hs
	$(GHC) $(HCFLAGS) Main.hs -o lenscal

clean:
	rm -f lenscal Main.hi Main.o
