#!/bin/bash
# Build the register machine into /app/bin/rvm.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/bin"
nim c --hints:off --warnings:off -d:release \
  --out:"$ROOT/bin/rvm" "$ROOT/src/main.nim"
