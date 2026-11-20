#!/usr/bin/env bash
set -euo pipefail
src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -m 0644 "$src_dir/main.cpp" /opt/hpackoscope/src/main.cpp
/opt/hpackoscope/scripts/build.sh
