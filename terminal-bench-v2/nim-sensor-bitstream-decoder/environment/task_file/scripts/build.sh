#!/bin/bash
# Compile the sensor-archive decoder into /app/bin/sardecode.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/bin"
nim c --hints:off --warnings:off -d:release \
  --out:"$ROOT/bin/sardecode" "$ROOT/src/main.nim"
