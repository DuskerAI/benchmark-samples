CXX ?= g++
CXXFLAGS ?= -std=c++20 -O2 -Wall -Wextra -pedantic
BIN := bin/cardrail_replay

all: $(BIN)

$(BIN): src/main.cpp
	mkdir -p bin
	$(CXX) $(CXXFLAGS) -Iinclude src/main.cpp -o $(BIN)

clean:
	rm -rf bin
