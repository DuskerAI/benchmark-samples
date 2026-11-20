#!/bin/bash
set -euo pipefail
cd /app/unitplan
mkdir -p bin
zig build-exe src/main.zig -O ReleaseSafe -femit-bin=bin/unitplan
