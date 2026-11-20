#!/bin/bash
set -euo pipefail
cd /app
mkdir -p /app/bin
go build -trimpath -o /app/bin/raft-recover ./cmd/raft-recover
