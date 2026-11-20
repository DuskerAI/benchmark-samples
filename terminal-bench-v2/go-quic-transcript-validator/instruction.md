Can you fix the Go validator in `/app/quicval`? It reads offline QUIC-style trace bundles and writes a JSON transcript report, but the current reports do not match the local packet contract.

Use the docs in `/app/quicval/docs/` as the source of truth. The build/run shape we need is: `go build -o /app/quicval/bin/quicval /app/quicval/cmd/quicval` and then `/app/quicval/bin/quicval --trace /app/quicval/fixtures/customer-alpha.qtr --out /app/quicval/out/transcript-report.json`.

Keep the report schema exactly as documented: deterministic ordering, normalized transport-parameter values, retry validation state, accepted-packet transcript entries, and the summary fields. The validator should work for other trace bundles with the same format, not just the bundled fixture. Do not modify trace fixture files under `/app/quicval/fixtures`.
