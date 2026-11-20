#!/bin/bash
set -euo pipefail
install -m 0644 /solution/main.fixed.c /app/certgate/src/main.c
/app/certgate/scripts/build.sh
