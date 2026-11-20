#!/bin/bash
if [ "$PWD" = "/" ]; then
  echo "Error: No working directory set."
  mkdir -p /logs/verifier
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi
mkdir -p /logs/verifier
python3 -m pytest -o cache_dir=/tmp/pytest_cache --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
RC=$?
if [ "$RC" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
