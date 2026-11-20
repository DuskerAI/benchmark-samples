#!/bin/bash
set -euo pipefail
mkdir -p /opt/quic-replay/bin
g++ -std=c++20 -O2 -Wall -Wextra -pedantic /opt/quic-replay/src/main.cpp -o /opt/quic-replay/bin/quic-replay
