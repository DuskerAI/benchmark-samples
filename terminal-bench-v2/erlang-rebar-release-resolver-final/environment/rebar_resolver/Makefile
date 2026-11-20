.PHONY: all clean

all:
	mkdir -p bin
	cp src/r3rel.erl bin/r3rel
	chmod +x bin/r3rel
	escript bin/r3rel --selfcheck

clean:
	rm -rf bin
