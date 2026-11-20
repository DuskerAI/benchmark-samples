#!/bin/bash
set -euo pipefail
cd /app/rtgate
make clean >/dev/null
make >/dev/null
