#!/bin/bash
set -euo pipefail

cd /app/acmegate
mkdir -p bin
cargo build --release --locked
cp target/release/acmegate bin/acmegate
