CXX := g++
CXXFLAGS := -std=c++20 -Wall -Wextra -Wpedantic -O2
BUILD := build
SRC := src/main.cpp src/notes.cpp
TEST_SRC := tests/test_notes.cpp src/notes.cpp

.PHONY: all test clean

all: $(BUILD)/note_summary

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/note_summary: $(SRC) src/notes.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) $(SRC) -o $@

$(BUILD)/test_notes: $(TEST_SRC) src/notes.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -Isrc $(TEST_SRC) -o $@

test: $(BUILD)/test_notes
	./$(BUILD)/test_notes

clean:
	rm -rf $(BUILD)
