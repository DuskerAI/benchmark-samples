#!/bin/bash
set -euo pipefail

root=/app/fwrestore
mkdir -p "$root/bin"
cp "$root/src/fwplan.scm" "$root/bin/fwplan"
chmod +x "$root/bin/fwplan"
