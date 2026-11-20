#!/bin/bash
set -euo pipefail
cd /app/minimap
mkdir -p zig-out/bin
zig build-exe src/main.zig -O ReleaseSafe -femit-bin=zig-out/bin/minimap
