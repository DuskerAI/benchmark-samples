#!/bin/bash
set -euo pipefail

install -m 0644 /solution/correct_main.nim /app/coseaudit/src/main.nim
make -C /app/coseaudit clean all
