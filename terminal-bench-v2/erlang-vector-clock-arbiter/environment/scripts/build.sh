#!/bin/sh
set -eu
mkdir -p /app/bin
cp /app/src/arbiter.erl /app/bin/arbiter
chmod 0755 /app/bin/arbiter
