#!/bin/sh
set -eu
mkdir -p /app/wgaudit/bin
nim c --hints:off --warnings:off --out:/app/wgaudit/bin/wgaudit /app/wgaudit/src/wgaudit.nim
