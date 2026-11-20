NIM ?= nim
PREFIX ?= /usr/local

.PHONY: all install clean

all: build/shogi-judge

build/shogi-judge: src/judge.nim
	mkdir -p build
	$(NIM) c -d:release --out:build/shogi-judge src/judge.nim

install: build/shogi-judge
	install -m 0755 build/shogi-judge $(PREFIX)/bin/shogi-judge

clean:
	rm -rf build
