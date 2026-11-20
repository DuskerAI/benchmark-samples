#!/bin/bash
set -euo pipefail
cd /app/firewall
mkdir -p bin
cargo build --release --offline
cp target/release/fwplan bin/fwplan
