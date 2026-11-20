#!/bin/bash
set -euo pipefail
cd /app
test "$(git rev-parse HEAD)" = 0ebcabfcb4edfbbdd18a4fa1a38485a59a5f6d7f
git apply --check /solution/gold_patch.diff
git apply /solution/gold_patch.diff
