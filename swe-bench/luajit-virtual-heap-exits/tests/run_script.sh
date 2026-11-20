#!/bin/bash
set -euo pipefail
exec /usr/bin/python3 -I -B /app/tests/test_jit_deopt.py
