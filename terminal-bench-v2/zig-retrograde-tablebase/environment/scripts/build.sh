#!/bin/bash
set -euo pipefail
mkdir -p /app/bin
zig build-exe /app/src/hexbase.zig -O ReleaseSafe -femit-bin=/app/bin/hexbase
