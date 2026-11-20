#!/bin/bash
set -euo pipefail
cd /app
test "$(git rev-parse HEAD)" = 620a6171118fa972c3b000b7c17f2d151175cde6
git apply --check /solution/gold_patch.diff
git apply /solution/gold_patch.diff
