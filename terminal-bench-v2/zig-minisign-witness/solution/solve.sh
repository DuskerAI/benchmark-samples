#!/bin/bash
set -euo pipefail
install -m 0644 /solution/main.zig /app/witnessgate/src/main.zig
/app/witnessgate/scripts/build.sh
