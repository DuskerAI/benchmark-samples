NIM ?= nim

.PHONY: all clean

all: bin/storegate

bin/storegate: src/storegate.nim
	mkdir -p bin
	$(NIM) c -d:release --out:bin/storegate src/storegate.nim

clean:
	rm -rf bin nimcache src/nimcache
