CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wpedantic

BUILD := build
BIN := $(BUILD)/cfi_unwind

.PHONY: all clean test smoke

all: $(BIN)

$(BIN): src/main.cpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $<

$(BUILD):
	mkdir -p $(BUILD)

smoke: $(BIN)
	python3 tools/make_smoke.py $(BUILD)
	$(BIN) $(BUILD)/smoke.crash $(BUILD)/smoke.json
	python3 tools/check_smoke.py $(BUILD)/smoke.json

test: smoke

clean:
	rm -rf $(BUILD)
