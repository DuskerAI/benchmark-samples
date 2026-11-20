CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Wpedantic
LDLIBS = -lcrypto

.PHONY: all clean
all: bin/flogaudit

bin/flogaudit: src/flogaudit.c
	mkdir -p bin
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -rf bin
