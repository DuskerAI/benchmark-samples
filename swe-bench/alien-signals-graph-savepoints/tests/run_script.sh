#!/bin/bash
set -euo pipefail
exec /usr/local/bin/python3 -I -B /tests/parser.py run
