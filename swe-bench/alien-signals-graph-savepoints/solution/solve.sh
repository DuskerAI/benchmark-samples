#!/bin/bash
set -euo pipefail
cd /app
test "$(git rev-parse HEAD)" = 995e375eb72e64b920e5c432590fb7a924cc9031
git apply --check /solution/gold_patch.diff
git apply /solution/gold_patch.diff
