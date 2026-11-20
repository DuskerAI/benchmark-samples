CXX ?= g++
CXXFLAGS ?= -std=c++20 -O2 -Wall -Wextra -pedantic

bin/kryofem: src/main.cpp | bin
	$(CXX) $(CXXFLAGS) -o $@ $<

bin:
	mkdir -p bin

.PHONY: all clean
all: bin/kryofem

clean:
	rm -rf bin
