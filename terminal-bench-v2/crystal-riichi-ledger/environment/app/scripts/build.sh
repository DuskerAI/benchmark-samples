#!/bin/bash
set -euo pipefail
mkdir -p /app/bin
crystal build /app/src/table_official.cr -o /app/bin/table-official
