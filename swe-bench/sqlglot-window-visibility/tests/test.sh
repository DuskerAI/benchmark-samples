#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /logs/verifier
chown root:root /logs/verifier
chmod 700 /logs/verifier
rm -f /logs/verifier/reward.txt /logs/verifier/reward.json /logs/verifier/results.json /logs/verifier/grade.json
printf '0\n' > /logs/verifier/reward.txt
cd /app
test "$(git rev-parse HEAD)" = 0ebcabfcb4edfbbdd18a4fa1a38485a59a5f6d7f
/usr/local/bin/python3 -I -B /tests/parser.py protect
rm -rf /app/tests
mkdir -p /app/tests
git apply --check /tests/test_patch.patch
git apply /tests/test_patch.patch
chown -R root:root /app/tests
chmod -R go-rwx /app/tests
/usr/local/bin/python3 -I -B /tests/parser.py integrity
set +e
bash /tests/run_script.sh > /logs/verifier/suite.log 2>&1
suite_exit=$?
set -e
printf '%s\n' "$suite_exit" > /logs/verifier/suite-exit-code.txt
/usr/local/bin/python3 -I -B /tests/parser.py grade
