#!/usr/bin/env python3
"""Verify packaged file hashes and catalog paths without executing task code."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def contained(base, relative):
    path = (base / relative).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"Path escapes package: {relative}")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", action="store_true", help="also verify Git bundle structure (does not apply patches)")
    args = parser.parse_args()
    checked = 0
    manifests = sorted(ROOT.glob("*/task-packages.sha256"))
    if not manifests:
        raise ValueError("No checksum manifests found")
    for manifest in manifests:
        for line in manifest.read_text().splitlines():
            expected, relative = line.split("  ", 1)
            path = contained(manifest.parent, relative)
            if digest(path) != expected:
                raise ValueError(f"Checksum mismatch: {path.relative_to(ROOT)}")
            checked += 1
    for path in sorted((ROOT / "swe-bench").glob("*/provenance.json")):
        record = json.loads(path.read_text())
        for relative, info in record["catalog_entry"]["files"].items():
            if digest(contained(path.parent, relative)) != info["sha256"]:
                raise ValueError(f"Source provenance mismatch: {path.parent.name}/{relative}")
    for path in sorted(ROOT.glob("*/catalog.json")):
        catalog = json.loads(path.read_text())
        if catalog["task_count"] != len(catalog["tasks"]):
            raise ValueError(f"Catalog count mismatch: {path}")
        ids = [entry["id"] for entry in catalog["tasks"]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate catalog ID: {path}")
        for entry in catalog["tasks"]:
            for key in ("path", "readme_path", "instruction_path"):
                if not contained(path.parent, entry[key]).exists():
                    raise ValueError(f"Missing catalog path: {entry[key]}")
    if args.bundles:
        seen = set()
        with tempfile.TemporaryDirectory(prefix="benchmark-bundles-") as tmp:
            subprocess.run(["git", "init", "--bare", "--quiet", tmp], check=True)
            for bundle in sorted(ROOT.rglob("*.bundle")):
                checksum = digest(bundle)
                if checksum in seen:
                    continue
                seen.add(checksum)
                result = subprocess.run(["git", "-C", tmp, "bundle", "verify", str(bundle)], capture_output=True, text=True)
                if result.returncode:
                    raise ValueError(f"Invalid or incomplete bundle: {bundle.relative_to(ROOT)}")
        print(f"Verified {len(seen)} distinct Git bundles")
    print(f"Verified {checked} manifest entries, SWE source hashes and catalog paths")
    print("Task execution, model results and patch applicability were not revalidated.")


if __name__ == "__main__":
    main()
