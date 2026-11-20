CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -pedantic

all: bin/hpackoscope

bin/hpackoscope: src/main.cpp
	mkdir -p bin
	$(CXX) $(CXXFLAGS) -o $@ $<

clean:
	rm -rf bin
