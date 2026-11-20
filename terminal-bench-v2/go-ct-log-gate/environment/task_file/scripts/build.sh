#!/bin/bash
set -euo pipefail

cd /app/ctgate
mkdir -p bin
go build -trimpath -o bin/ctgate ./cmd/ctgate
