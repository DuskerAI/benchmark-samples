#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/bin"
g++ -std=c++20 -O2 -Wall -Wextra -pedantic "$ROOT/src/main.cpp" -o "$ROOT/bin/relplan"
