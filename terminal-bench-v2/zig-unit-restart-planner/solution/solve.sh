#!/bin/bash
set -euo pipefail
cp /solution/main.zig /app/unitplan/src/main.zig
/app/unitplan/scripts/build.sh
