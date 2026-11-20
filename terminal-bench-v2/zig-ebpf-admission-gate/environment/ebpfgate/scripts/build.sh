#!/bin/bash
set -euo pipefail
mkdir -p /app/ebpfgate/bin
zig build-exe /app/ebpfgate/src/main.zig -O ReleaseSafe -femit-bin=/app/ebpfgate/bin/ebpfgate
