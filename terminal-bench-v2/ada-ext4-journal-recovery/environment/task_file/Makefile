GNATMAKE ?= gnatmake

all: bin/fsrecover

bin/fsrecover: src/fsrecover.adb
	mkdir -p bin
	cd bin && $(GNATMAKE) -gnat2020 -O0 -g -o fsrecover ../src/fsrecover.adb

clean:
	rm -f bin/fsrecover bin/*.ali bin/*.o src/*.ali src/*.o
