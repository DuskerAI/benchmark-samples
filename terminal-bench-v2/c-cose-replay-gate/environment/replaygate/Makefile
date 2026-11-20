CC ?= gcc
CFLAGS ?= -std=c11 -Wall -Wextra -O2
LDFLAGS ?= -lcrypto

SRC = src/replaygate.c
BIN = bin/replaygate

.PHONY: all clean

all: $(BIN)

$(BIN): $(SRC)
	mkdir -p bin
	$(CC) $(CFLAGS) -o $(BIN) $(SRC) $(LDFLAGS)

clean:
	rm -rf bin
