#!/bin/bash
set -euo pipefail
cd /app
test "$(git rev-parse HEAD)" = 8290e0a52ebcb5225a2dcfe154dff5b97054481f
git apply --check /solution/gold_patch.diff
git apply /solution/gold_patch.diff
