#!/bin/bash
set -euo pipefail
install -m 0644 /solution/rtgate.adb.fixed /app/rtgate/src/rtgate.adb
/app/rtgate/scripts/build.sh
