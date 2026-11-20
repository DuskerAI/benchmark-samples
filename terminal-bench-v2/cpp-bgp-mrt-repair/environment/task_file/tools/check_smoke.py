#!/usr/bin/env python3
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["records"]["applied"] == 2
assert data["selected_count"] == 1
assert data["routes"][0]["prefix"] == "203.0.113.0/24"
