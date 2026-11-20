#!/bin/bash
set -euo pipefail
cd /app/fidogate
make clean >/dev/null 2>&1 || true
make
