Fix the Rust project in `/app/task_file` so `walfix replay` reconstructs Binary Watershed Log state correctly.

Use `/app/task_file/README.md` as the normative contract for the file format, recovery behavior, and JSON output. Preserve the `walfix replay [--json] <segments...>` command-line interface. Your changes should make the existing Rust tests and the verifier pass without requiring network access.
