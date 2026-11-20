#!/bin/bash
set -euo pipefail

install -m 0644 /solution/correct-index.ts /app/arena/src/index.ts
/app/arena/scripts/build.sh
