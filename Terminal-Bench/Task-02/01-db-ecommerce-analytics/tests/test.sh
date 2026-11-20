#!/bin/bash
# Verify input data has not been tampered with
cd /app/input_data
sha256sum -c checksums.sha256 --quiet
if [ $? -ne 0 ]; then
    echo "Input data checksum verification failed — aborting." >&2
    echo 0 > /logs/verifier/reward.txt
    exit 1
fi
cd /

pip install pytest==8.3.4 duckdb==1.1.3 -q

python -m pytest /tests/test_outputs.py -v -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
