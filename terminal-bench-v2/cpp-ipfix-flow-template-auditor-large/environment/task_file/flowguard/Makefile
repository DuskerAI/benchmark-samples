CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -pedantic
BIN_DIR := bin
SRC := src/main.cpp
TARGET := $(BIN_DIR)/flowguard

all: $(TARGET)

$(TARGET): $(SRC)
	mkdir -p $(BIN_DIR)
	$(CXX) $(CXXFLAGS) -Iinclude $(SRC) -o $(TARGET)

clean:
	rm -rf $(BIN_DIR)

.PHONY: all clean
