CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror

.PHONY: all clean

all: build/uring_replay

build/uring_replay: src/replay.c | build
	$(CC) $(CFLAGS) -o $@ $<

build:
	mkdir -p build

clean:
	rm -rf build
