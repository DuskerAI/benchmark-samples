#!/bin/sh
set -eu
mkdir -p /app/wasmguard/bin
raco exe -o /app/wasmguard/bin/wasmguard /app/wasmguard/src/guard.rkt
