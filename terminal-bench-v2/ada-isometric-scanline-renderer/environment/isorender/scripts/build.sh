#!/bin/bash
set -euo pipefail
cd /app/isorender
mkdir -p bin
gnatmake -gnat2020 -O2 -o bin/isoboard src/isoboard.adb
