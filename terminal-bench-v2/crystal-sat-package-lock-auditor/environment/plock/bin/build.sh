#!/bin/sh
set -eu
mkdir -p /app/plock/bin
crystal build /app/plock/src/plock_audit.cr -o /app/plock/bin/plock-audit --release
