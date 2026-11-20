GNATMAKE ?= gnatmake
SRC := src/btraudit.adb
BIN := bin/btraudit

.PHONY: all clean

all: $(BIN)

$(BIN): $(SRC)
	mkdir -p bin obj
	$(GNATMAKE) -q -D obj -o $(BIN) $(SRC)

clean:
	rm -rf bin obj
