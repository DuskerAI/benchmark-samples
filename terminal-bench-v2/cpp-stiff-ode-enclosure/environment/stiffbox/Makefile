CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -pedantic

.PHONY: all clean

all: bin/stiffbox

bin/stiffbox: src/main.cpp
	mkdir -p bin
	$(CXX) $(CXXFLAGS) $< -o $@

clean:
	rm -rf bin
