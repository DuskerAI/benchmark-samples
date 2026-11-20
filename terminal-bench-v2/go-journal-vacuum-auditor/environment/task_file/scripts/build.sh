#!/bin/bash
set -euo pipefail
cd /app/jvac
mkdir -p bin
go build -o bin/jvac ./cmd/jvac
