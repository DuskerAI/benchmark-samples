#!/bin/bash
set -euo pipefail
mkdir -p /app/bin
zig build-exe /app/src/main.zig -O ReleaseSafe -femit-bin=/app/bin/dungeon_render
