CC ?= gcc
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O2 -g

.PHONY: all clean test

all: build/arena_replay

build/arena_replay: src/replay.c
	mkdir -p build
	$(CC) $(CFLAGS) -o $@ $<

test: build/arena_replay
	./build/arena_replay examples/smoke.art /tmp/arena-smoke.json >/dev/null
	test -s /tmp/arena-smoke.json

clean:
	rm -rf build
