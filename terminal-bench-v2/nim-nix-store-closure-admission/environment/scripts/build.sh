#!/bin/bash
set -euo pipefail
cd /app/storegate
make clean >/dev/null
make >/dev/null
