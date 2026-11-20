CC ?= gcc
CFLAGS ?= -std=c11 -Wall -Wextra -O2
LDFLAGS ?=

all: bin/ttynorm bin/ttyd

bin:
	mkdir -p bin

bin/ttynorm: src/ttynorm.c src/replay.c src/replay.h | bin
	$(CC) $(CFLAGS) -o $@ src/ttynorm.c src/replay.c $(LDFLAGS)

bin/ttyd: src/server.c src/replay.c src/replay.h | bin
	$(CC) $(CFLAGS) -o $@ src/server.c src/replay.c $(LDFLAGS)

clean:
	rm -rf bin

.PHONY: all clean
