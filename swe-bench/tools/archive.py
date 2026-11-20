#!/usr/bin/env python3
"""Verify and restore deduplicated execution captures without running their code."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def inventory(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Expected a regular directory: " + str(directory))
    files = {}
    for path in sorted(directory.rglob("*")):
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError("Unsupported archive entry: " + str(path))
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {
        "tree_sha256": hashlib.sha256(encoded).hexdigest(),
        "file_count": len(files),
        "bytes": sum(value["bytes"] for value in files.values()),
        "files": files,
    }


def resolve(directory, root=ROOT):
    root = Path(root).resolve()
    directory = Path(directory).absolute()
    if directory.is_symlink():
        raise ValueError("Archive paths cannot be symlinks")
    directory = directory.resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Archive path leaves repository")
    relative = directory.relative_to(root)
    boundary = root
    if relative.parts and re.fullmatch(r"dataset[1-9][0-9]*", relative.parts[0]):
        boundary = root / relative.parts[0]
    if directory.is_dir():
        inventory(directory)
        return directory
    reference = directory.with_name(directory.name + ".reference.json")
    if reference.is_symlink():
        raise ValueError("Archive references cannot be symlinks")
    record = json.loads(reference.read_text())
    target = (reference.parent / record["retained_directory"]).resolve()
    if not target.is_relative_to(boundary):
        raise ValueError("Archive target leaves its dataset")
    actual = inventory(target)
    for key in ("tree_sha256", "file_count", "bytes"):
        if actual[key] != record.get(key, record.get("original_" + key)):
            raise ValueError("Archive checksum mismatch: " + str(reference))
    return target


def restore(directory, root=ROOT):
    directory = Path(directory).absolute()
    if directory.exists() or directory.is_symlink():
        raise ValueError("Refusing to overwrite: " + str(directory))
    source = resolve(directory, root)
    expected = inventory(source)
    # Build a verified temporary copy so a failed copy never becomes a capture.
    with tempfile.TemporaryDirectory(prefix=".archive-", dir=directory.parent) as tmp:
        staged = Path(tmp) / "capture"
        shutil.copytree(source, staged)
        if inventory(staged) != expected:
            raise ValueError("Restored archive differs from retained capture")
        if directory.exists() or directory.is_symlink():
            raise ValueError("Restore destination appeared during copy")
        staged.rename(directory)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "restore"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        target = restore(args.directory) if args.action == "restore" else resolve(args.directory)
        result = {k: v for k, v in inventory(target).items() if k != "files"}
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, "Archive check failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
