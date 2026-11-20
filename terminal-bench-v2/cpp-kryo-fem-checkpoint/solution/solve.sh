#!/bin/bash
set -euo pipefail

cp /solution/main.cpp /app/kryofem/src/main.cpp
make -C /app/kryofem clean all
