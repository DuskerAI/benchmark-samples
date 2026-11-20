CC ?= gcc
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O2

.PHONY: all clean

all: bin/fidogate

bin/fidogate: src/main.c
	@mkdir -p bin
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -rf bin
