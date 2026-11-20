CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror -pedantic
LDLIBS ?= -lcrypto

.PHONY: all clean
all: bin/certgate

bin/certgate: src/main.c
	mkdir -p bin
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -rf bin
