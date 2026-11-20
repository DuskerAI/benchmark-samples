GNATMAKE ?= gnatmake
SRC = src/rtgate.adb
OUT = bin/rtgate

.PHONY: all clean

all: $(OUT)

$(OUT): $(SRC) src/crypto_hash.ads src/crypto_hash.adb
	mkdir -p bin
	$(GNATMAKE) -gnat2020 -O2 -Isrc -o $(OUT) $(SRC) -largs -lcrypto

clean:
	rm -rf bin src/*.ali src/*.o
