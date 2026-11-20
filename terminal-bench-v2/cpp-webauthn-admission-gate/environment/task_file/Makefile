CXX ?= g++
CXXFLAGS ?= -std=c++17 -Wall -Wextra -O2
LDFLAGS ?= -lcrypto

SRC := src/main.cpp
BIN := bin/webauthn-gate

.PHONY: all clean

all: $(BIN)

$(BIN): $(SRC)
	mkdir -p bin
	$(CXX) $(CXXFLAGS) -o $(BIN) $(SRC) $(LDFLAGS)

clean:
	rm -rf bin
