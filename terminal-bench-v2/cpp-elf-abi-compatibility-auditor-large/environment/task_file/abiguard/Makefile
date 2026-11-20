CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -pedantic
LDFLAGS ?=
SRC := src/main.cpp
BIN := bin/abiguard

.PHONY: all clean

all: $(BIN)

$(BIN): $(SRC)
	mkdir -p bin
	$(CXX) $(CXXFLAGS) -Iinclude -o $@ $(SRC) $(LDFLAGS)

clean:
	rm -rf bin
