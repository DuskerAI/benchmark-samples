# Resetting a workflow can lose history or consume an abandoned result

The SQLite recovery prototype starts a fresh execution at an earlier committed
task boundary without repeating external side effects. It works for a simple
reset, but nested resets lose history, completed activities can run again, and a
late result can satisfy a different command after a reset reuses its numeric
schedule ID. See `INCIDENT-RESET.md`, `backend/sqlite/RESET.md` and the public
reproduction in `backend/sqlite/incident_reset_test.go`.

Repair the reset boundary so the executor and backend agree about both the
selected history and the external work it owns. Follow the complete recovery
contract in `backend/sqlite/RESET.md`:

- Share the selected prefix across arbitrary nested resets, including cuts before
  an inherited boundary. Preserve event identity, attributes, logical sequence and
  suffix reads. Detect invalid cuts and incomplete or corrupt ancestry.
- Adopt retained outstanding activity/timer/child publications. Preserve activity
  leases and child parent identities; reuse already accepted results without
  reissuing work. Results from abandoned publications cannot satisfy newly
  scheduled work that happens to reuse a command slot. Completion retries must
  preserve their ordinary idempotent/conflict behavior.
- Recover post-cut external inputs in order, including consumed and pending
  signals and accepted activity/child results. Restore original timer delivery
  after a post-cut local cancellation, without causing a second logical firing.
- Reset atomically and retry it without writes. Preserve fencing of old workflow
  acquisitions. Reject invalid reset requests without partial changes.
- Keep the ancestry needed by retained branches. Removing a referenced source
  must fail safely; retention must reclaim eligible leaves and ancestors without
  stranding newer executions or resurrecting removed ones.

Ordinary execution, inline replay and checkpoint attempt fencing already work in
this baseline. Preserve them, the public method signatures, event wire format,
module/dependency manifests, queues and configured payload converters. Keep warm
execution supported. New helpers and SQLite migrations are supported. No changes
to other storage backends or external services are required.

Go 1.25.3, cached dependencies and embedded SQLite are available offline:

```sh
go test -p 2 -short ./backend/sqlite ./internal/worker ./workflow/...
```

Production sources under the existing runtime package roots are exported to a
separate verifier. New source files/subpackages there are supported. Submitted
tests and generated binaries do not replace trusted grading; the source limit
is 16 MiB.
