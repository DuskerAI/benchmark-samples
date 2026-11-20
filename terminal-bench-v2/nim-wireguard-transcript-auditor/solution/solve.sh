#!/bin/bash
set -euo pipefail

install -m 0644 /solution/correct_wgaudit.nim /app/wgaudit/src/wgaudit.nim
chmod +x /app/wgaudit/build.sh
/app/wgaudit/build.sh
