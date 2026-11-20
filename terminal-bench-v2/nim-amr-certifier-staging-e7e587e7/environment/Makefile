NIM ?= nim

all: bin/amrcert

bin/amrcert: src/amrcert.nim
	mkdir -p bin
	$(NIM) c -d:release --out:bin/amrcert src/amrcert.nim

clean:
	rm -rf bin
