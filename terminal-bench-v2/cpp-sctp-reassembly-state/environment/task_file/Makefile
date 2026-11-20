CXX := g++
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -pedantic
BUILD := build

SRC := src/main.cpp src/reassembler.cpp
OBJ := $(patsubst src/%.cpp,$(BUILD)/%.o,$(SRC))

.PHONY: all clean test

all: $(BUILD)/sctp_replay

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/%.o: src/%.cpp src/reassembler.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(BUILD)/sctp_replay: $(OBJ)
	$(CXX) $(CXXFLAGS) $(OBJ) -o $@

test: $(BUILD)/sctp_replay
	$(BUILD)/sctp_replay examples/smoke.trace /tmp/sctp_smoke.json
	test -s /tmp/sctp_smoke.json

clean:
	rm -rf $(BUILD)
