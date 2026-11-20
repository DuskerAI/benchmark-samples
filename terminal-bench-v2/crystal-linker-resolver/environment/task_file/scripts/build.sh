#!/bin/bash
# Build the crlink static linker binary from the Crystal sources.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p bin
crystal build src/main.cr -o bin/crlink
