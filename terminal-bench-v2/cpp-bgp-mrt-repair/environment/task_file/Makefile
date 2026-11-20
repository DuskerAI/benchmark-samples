CXX := g++
CXXFLAGS := -std=c++20 -O2 -Wall -Wextra -pedantic

SRC := src/main.cpp
BIN := build/mrt_replay

.PHONY: all clean test

all: $(BIN)

$(BIN): $(SRC)
	@mkdir -p build
	$(CXX) $(CXXFLAGS) -o $@ $<

test: $(BIN)
	./$(BIN) fixtures/smoke.mrt build/smoke.json
	python3 tools/check_smoke.py build/smoke.json

clean:
	rm -rf build
