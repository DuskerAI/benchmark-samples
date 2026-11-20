#!/bin/bash
set -euo pipefail
cp /solution/fidogate_main.c /app/fidogate/src/main.c
/app/fidogate/scripts/build.sh
