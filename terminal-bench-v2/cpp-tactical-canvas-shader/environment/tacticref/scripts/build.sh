#!/bin/bash
set -euo pipefail
mkdir -p /app/tacticref/bin
g++ -std=c++17 -O2 -Wall -Wextra -pedantic /app/tacticref/src/main.cpp -o /app/tacticref/bin/tactics_referee
