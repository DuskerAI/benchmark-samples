#!/bin/bash
set -euo pipefail

cd /app/josegate
mkdir -p bin
go build -o /app/josegate/bin/josegate /app/josegate/cmd/josegate
