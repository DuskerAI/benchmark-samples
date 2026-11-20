Use `/app/task_file` for offline Raft trace report:

`go run ./cmd/raftreport --input /app/task_file/traces --output /app/task_file/report.json`

Rules:
- Read traces lexically.
- Per node, non-stale `install_snapshot` (`last_index` > current snapshot index) replaces compacted prefix and increments trace `snapshots`; stale installs leave state/counters unchanged.
- `append_entries` must match `prev_index`/`prev_term` against log or snapshot. On prefix failure, reject the batch and count every entry as `ignored_entries`. Entries at/below that node's snapshot index are ignored and counted; suffix replacements are not ignored.
- `crash_cut` alone commits later log entries; cuts never roll state backward. Log entries need voting quorum over identical normalized full payloads. Joint configs also need incoming-voter majority. Learners do not vote; committed configs update membership.
- Snapshots commit only with quorum of identical index, term, kv, sessions, and membership under that snapshot membership.
- Repeated client sequences return the prior result and increment only `duplicates`.

Commands apply in index order; absent keys read as `0`. `set` stores/returns `value`; `incr` adds `delta`; `delete` returns/removes the previous value; `cas` stores `value` and returns `1` iff current equals `expect`, otherwise returns `0` unchanged.

Output top level is `traces` and `totals`. Trace fields: `name`, integer `committed_index`/`last_term`, `kv`, `sessions` `{seq,response,request_id}`, `membership` `{voters,learners,phase, optional outgoing_voters}`, integer `counters` `{commands,configs,snapshots,duplicates,ignored_entries}`. `totals` has integer `traces`, `committed_entries`, `snapshots`, `duplicates`; `committed_entries` sums committed non-duplicate commands/configs. Keep CLI flags, paths, and schema unchanged.
