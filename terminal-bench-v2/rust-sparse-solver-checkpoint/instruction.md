The Rust project in `/app/task_file` builds a command-line sparse linear solver called `spsolve`. Repair it so `cargo run --locked -- run <job-file> <report-file>` follows the contract in `/app/task_file/README.md`.

Keep the command shape and report JSON schema unchanged. The verifier will build the project, run generated jobs with legal MatrixMarket inputs and checkpoint reuse, and check the reported solutions against the original systems. No network access is available.
