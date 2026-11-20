CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror -D_DEFAULT_SOURCE
LDFLAGS ?=

.PHONY: all clean

all: bin/logview bin/buildlogd

bin:
	mkdir -p bin

bin/logview: src/main.c src/logview.c src/logview.h | bin
	$(CC) $(CFLAGS) -o $@ src/main.c src/logview.c $(LDFLAGS)

bin/buildlogd: src/server.c src/logview.c src/logview.h | bin
	$(CC) $(CFLAGS) -o $@ src/server.c src/logview.c $(LDFLAGS)

clean:
	rm -rf bin
