.PHONY: all clean

all:
	dune build bin/rekor_gate.exe
	mkdir -p bin
	cp _build/default/bin/rekor_gate.exe bin/rekor-gate

clean:
	dune clean
	rm -f bin/rekor-gate
