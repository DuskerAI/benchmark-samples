#!/usr/bin/env python3
import json
import pathlib
import sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data["summary"]["thread_count"] == 1
assert data["summary"]["complete_threads"] == 1
assert data["threads"][0]["frames"][0]["status"] == "root"
