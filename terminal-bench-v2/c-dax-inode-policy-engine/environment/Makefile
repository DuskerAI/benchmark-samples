CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror -Iinclude
OBJ = build/main.o build/model.o build/policy.o build/plan.o build/report.o

all: build/daxadmin

build:
	mkdir -p build

build/%.o: src/%.c | build
	$(CC) $(CFLAGS) -c $< -o $@

build/daxadmin: $(OBJ)
	$(CC) $(CFLAGS) $(OBJ) -o $@

clean:
	rm -rf build

.PHONY: all clean
