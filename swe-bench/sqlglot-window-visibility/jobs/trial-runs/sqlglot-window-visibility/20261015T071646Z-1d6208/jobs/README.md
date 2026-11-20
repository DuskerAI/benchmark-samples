# Archived execution records

These records preserve original run configurations, results, and retained source
captures. Execution-time paths in native records may refer to a historical layout.
Current navigation is provided by the enclosing dataset's `jobs/index.json`.

Duplicate captures use `*.reference.json` with a relative retained target and
verified content hashes. From the repository root, use
`python3 -B tools/archive.py verify PATH_TO_MISSING_DIRECTORY` or replace `verify`
with `restore` to materialize a capture. Repository checks run with
`python3 -B tools/validate.py`.
