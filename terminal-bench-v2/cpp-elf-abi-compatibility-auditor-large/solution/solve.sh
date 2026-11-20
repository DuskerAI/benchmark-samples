#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$ROOT_DIR/fixes/main.cpp" /app/abiguard/src/main.cpp
cd /app/abiguard
make clean all
rm -rf /var/lib/abiguard/output
mkdir -p /var/lib/abiguard/output
/app/abiguard/bin/abiguard \
  --policy /etc/abiguard/policy.toml \
  --baseline /var/lib/abiguard/baselines \
  --release /var/lib/abiguard/releases/current \
  --output /var/lib/abiguard/output
test -s /var/lib/abiguard/output/abi_report.json
test -s /var/lib/abiguard/output/soname_matrix.csv
test -s /var/lib/abiguard/output/risk_trace.jsonl
