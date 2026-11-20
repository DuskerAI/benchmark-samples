GNATMAKE ?= gnatmake
SRC = src/pcrgate.adb
OUT = bin/pcrgate

.PHONY: all clean

all: $(OUT)

$(OUT): $(SRC) src/crypto_hash.ads src/crypto_hash.adb
	mkdir -p bin
	$(GNATMAKE) -gnat2020 -O2 -Isrc -o $(OUT) $(SRC) -largs -lcrypto

clean:
	rm -rf bin src/*.ali src/*.o
