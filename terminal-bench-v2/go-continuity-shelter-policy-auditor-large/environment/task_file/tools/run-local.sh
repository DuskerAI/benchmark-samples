#!/usr/bin/env bash
set -euo pipefail
cd /app/task_file
go build -o /app/task_file/bin/continuity-audit /app/task_file/cmd/continuity-audit
/app/task_file/bin/continuity-audit --policy-root /opt/continuity-policy-corpus --case-root /var/lib/continuity-audit/cases --config /etc/continuity-audit/config.json --out-dir /var/lib/continuity-audit/output
