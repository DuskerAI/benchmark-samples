The C replay tool in `/app/task_file` should rebuild an arena allocator's state from local trace files. Right now its reports are wrong on crash-style inputs, including traces that stop after a partial final record.

Fix `/app/task_file` so it follows `/app/task_file/README.md`. Keep the interface as `cd /app/task_file && make`, then `/app/task_file/build/arena_replay <trace-file> <report-json>`. The verifier will build the project and run new traces through the binary, comparing the generated JSON report with the full replay contract, so every tested trace must match that contract exactly.

The initial `ARENA` record defines the arena; only valid `ALLOC` and `FREE` records count as applied operations. Do not require network access or write outside the report path passed on the command line.
