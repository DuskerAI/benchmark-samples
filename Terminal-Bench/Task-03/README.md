# Task 03 — Event-Sourcing Compactor: Concurrency Repair (TypeScript + PostgreSQL)

**Suite:** Terminal-Bench · **Source task ID:** `09-event-sourcing-compactor` · **Category:** debugging (api_integration, db_interaction) · **Languages:** TypeScript, SQL
**Difficulty:** hard · **Expert / junior estimate:** 90 / 900 min · **Verifier:** 23 black-box HTTP tests (incl. concurrency) + process rubric

| Model (pass@4) | Passes | Solved |
| --- | :---: | :---: |
| Claude Opus 4.8 | **4 / 4** | Yes |
| GPT-5.5 | **4 / 4** | Yes |

---

## Objective

An event-sourced order service (Express + `pg`, PostgreSQL 16) is corrupting state in production
after a new compaction job shipped. Symptoms include confirmed orders reading back as `pending`, items
disappearing after compaction under load, and "successful" compactions leaving wrong totals. The agent
must find and fix the defects in a running service. The compactor is the stated primary suspect, but
the read path is also implicated. The service must stay API-compatible.

The prompt is [`instruction.md`](09-event-sourcing-compactor/instruction.md). The service source is in
[`environment/task_file/src/`](09-event-sourcing-compactor/environment/task_file/src/).

## Technical challenges

The defects are **distributed-systems bugs**. They only surface under interleaving, so reading the code
for logic errors is not enough.

| Defect | Location | Consequence |
| --- | --- | --- |
| Snapshot + event read happens *outside* the write transaction (check-then-act) | `compactor.ts` | Concurrent writes between read and prune are folded inconsistently or lost |
| Snapshot upsert overwrites unconditionally | `compactor.ts` | A slower, older compaction can **downgrade** a newer snapshot, then its prune deletes events the old snapshot never folded in |
| No version-sequence validation | `compactor.ts` | Gaps in the event stream are silently folded into a snapshot |
| Snapshot and tail events are read in two non-isolated queries | `event-store.ts` (`loadAggregate`) | A compaction that commits between the two reads yields an old snapshot with already-pruned events, so state regresses |

A correct repair needs transactional reads at an adequate isolation level, a version-guarded upsert
(`ON CONFLICT … WHERE snapshots.version < EXCLUDED.version`), explicit gap detection (`"Missing event"`),
and consistent snapshot-plus-tail reads on the query path. All of this must preserve the response
contracts of the compaction and order endpoints.

## Environment

A single image combines PostgreSQL and Node. Everything runs offline after build.

| Item | Value |
| --- | --- |
| Build | Multi-stage: `node:20.11.0-bookworm-slim` builder (`npm ci`, `tsc`) → runtime on `postgres:16-bookworm` + Node 20 |
| Startup | [`entrypoint.sh`](09-event-sourcing-compactor/environment/entrypoint.sh) initialises Postgres, applies [`001_schema.sql`](09-event-sourcing-compactor/environment/task_file/migrations/001_schema.sql), runs the service in a restart loop (so the agent can rebuild and kill it) |
| Orchestration | Custom [`docker-compose.yaml`](09-event-sourcing-compactor/environment/docker-compose.yaml) (`custom_docker_compose = true`) |
| Offline rebuild | `node_modules` copied from the builder so `npm run build` works without network |
| Resources | 2 CPUs · 2 GB RAM · 10 GB storage |
| Timeouts | agent 1200 s · verifier 300 s · build 600 s |

## Verification methodology

[`tests/test.sh`](09-event-sourcing-compactor/tests/test.sh) waits for the service to report healthy. It
then **rebuilds the agent's TypeScript**, restarts the service so the verified binary is the agent's
current source, and runs [`tests/test_outputs.py`](09-event-sourcing-compactor/tests/test_outputs.py)
over HTTP. Reward is binary.

| Test group | Tests | What it exercises |
| --- | :---: | --- |
| `TestBasicOperations` | 7 | CRUD endpoints, empty-order confirm rejected (HTTP 400), compaction response shape |
| `TestCompactionPreservesState` | 4 | State identical before/after compaction; repeated compaction stable; post-compaction events applied |
| `TestConcurrentCompaction` | 3 | Snapshot never downgrades; compaction racing writes; all items survive parallel compactions |
| `TestReadDuringCompaction` | 2 | Reads never return stale state; status never regresses while compacting |
| `TestVersionGap` | 4 | Gaps at start / middle / above an existing snapshot fail with `Missing event`; no false positives |
| `TestIntegration` | 3 | `POST /admin/compact` (all aggregates), snapshot version correctness, full lifecycle |

**Process rubric.** [`rubrics.txt`](09-event-sourcing-compactor/rubrics.txt) defines weighted trajectory
criteria for qualitative or LLM-judge grading. Positive criteria include reading both source files
before editing, identifying that the bugs span two files, applying `REPEATABLE READ` on the read path,
adding a version guard, and testing under concurrent load. Penalties cover unnecessary edits,
destructive commands, hard-coding, and disabling pruning. The rubric is **not** part of the binary
reward, and no rubric scores are recorded for the runs below.

## Evaluation results

GPT-5.5 used high reasoning effort. The Opus 4.8 attempts used the harness default
(see [`../README.md`](../README.md#evaluation-protocol)).

| Model | Trial | Reward | Tests passed |
| --- | --- | :---: | :---: |
| Claude Opus 4.8 | [`phEMv3J`](evaluation/claude-opus-4.8/pass4-batch-08-09/09-event-sourcing-compactor__phEMv3J/verifier/test-stdout.txt) | 1 | 23 / 23 |
| Claude Opus 4.8 | [`MkNRePc`](evaluation/claude-opus-4.8/pass4-batch-08-09/09-event-sourcing-compactor__MkNRePc/verifier/test-stdout.txt) | 1 | 23 / 23 |
| Claude Opus 4.8 | [`Ui2hBGu`](evaluation/claude-opus-4.8/pass4-batch-08-09/09-event-sourcing-compactor__Ui2hBGu/verifier/test-stdout.txt) | 1 | 23 / 23 |
| Claude Opus 4.8 | [`FpwPo82`](evaluation/claude-opus-4.8/pass4-batch-08-09/09-event-sourcing-compactor__FpwPo82/verifier/test-stdout.txt) | 1 | 23 / 23 |
| GPT-5.5 | [`5C2pL2p`](evaluation/gpt-5.5/09-event-sourcing-compactor__5C2pL2p/verifier/test-stdout.txt) | 1 | 23 / 23 |
| GPT-5.5 | [`9gSTFfy`](evaluation/gpt-5.5/09-event-sourcing-compactor__9gSTFfy/verifier/test-stdout.txt) | 1 | 23 / 23 |
| GPT-5.5 | [`NeVjkn8`](evaluation/gpt-5.5/09-event-sourcing-compactor__NeVjkn8/verifier/test-stdout.txt) | 1 | 23 / 23 |
| GPT-5.5 | [`hJDUXMz`](evaluation/gpt-5.5/09-event-sourcing-compactor__hJDUXMz/verifier/test-stdout.txt) | 1 | 23 / 23 |

**Pooled:** 8 / 8 trials passed.

### Interpretation

Both frontier models solved every attempt, so this task does **not** discriminate between them at
pass@4. It is included for its environment and verifier design. It is a stateful, multi-process service
with a real database, a black-box concurrency test suite, a verifier that rebuilds and restarts the
service before grading, and a process rubric for trajectory-level assessment. It is a strong candidate
for harder variants, for example removing the "read path may also be involved" hint or adding
isolation-anomaly tests under higher contention.

## Reproduce

```sh
harbor run -p 09-event-sourcing-compactor -a oracle   # reference fix → expected reward 1
harbor run -p 09-event-sourcing-compactor -a nop      # buggy service → expected reward 0
```

The reference fix is [`solution/solve.sh`](09-event-sourcing-compactor/solution/solve.sh). It rewrites
`compactor.ts` and `event-store.ts`, rebuilds, and restarts the service.

## Contents

```text
Task-03/
├── 09-event-sourcing-compactor/      Runnable task package (unchanged from source)
│   ├── instruction.md · task.toml · rubrics.txt
│   ├── environment/                  Dockerfile, docker-compose.yaml, entrypoint.sh, task_file/ (service source)
│   ├── solution/solve.sh             Reference (oracle) fix
│   └── tests/                        test.sh + test_outputs.py (verifier)
└── evaluation/
    ├── claude-opus-4.8/pass4-batch-08-09/<trial>/
    └── gpt-5.5/<trial>/
```
