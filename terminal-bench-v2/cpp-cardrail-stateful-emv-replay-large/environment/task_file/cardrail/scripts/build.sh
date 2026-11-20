#!/usr/bin/env bash
set -euo pipefail
cd /app/cardrail
make clean >/dev/null 2>&1 || true
make all
