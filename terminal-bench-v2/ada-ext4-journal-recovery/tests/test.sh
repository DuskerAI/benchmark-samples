#!/bin/bash
set -uo pipefail

if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."
    mkdir -p /logs/verifier
    echo 0 > /logs/verifier/reward.txt
    exit 0
fi

mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt

run_pytest_with_ctrf_validation() {
    rm -f /logs/verifier/ctrf.json
    /opt/verifier/bin/python -m pytest -o cache_dir=/tmp/pytest_cache --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
    pytest_rc=$?
    /opt/verifier/bin/python - <<'PY'
import ast
import json
import sys
from pathlib import Path

ctrf_path = Path("/logs/verifier/ctrf.json")
test_path = Path("/tests/test_outputs.py")
if not ctrf_path.exists() or ctrf_path.stat().st_size == 0:
    sys.exit(1)

tree = ast.parse(test_path.read_text())
expected = sum(isinstance(node, ast.FunctionDef) and node.name.startswith("test_") for node in tree.body)
data = json.loads(ctrf_path.read_text())
root = data.get("results", data)
tests = root.get("tests", [])
summary = root.get("summary", {})
reported = summary.get("tests", summary.get("total", len(tests)))
passed = summary.get("passed")
failed = sum(summary.get(key, 0) for key in ("failed", "skipped", "pending", "other"))

if reported != expected or len(tests) != expected or failed != 0:
    sys.exit(1)
if passed is not None and passed != expected:
    sys.exit(1)
PY
    ctrf_rc=$?
    [ "$pytest_rc" -eq 0 ] && [ "$ctrf_rc" -eq 0 ]
}

run_pytest_with_ctrf_validation
rc=$?
if [ "$rc" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
