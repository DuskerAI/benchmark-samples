# Alien Signals: Nested Transactional Savepoints for a Reactive Graph

**Suite:** SWE-bench-style repository task · **Task ID:** `dusker-ai/alien-signals-graph-savepoints` v1.0.0 · **Category:** reactive systems & dataflow · **Language:** TypeScript
**Upstream:** [stackblitz/alien-signals](https://github.com/stackblitz/alien-signals) @ `c00e6396` · **Baseline commit:** `995e375e` · **Tests:** 118 fail-to-pass + 10 pass-to-pass in 33 requirement groups (each run against CJS and ESM builds)

| Model | Trials | Passed | Fail-to-pass (best) |
| --- | :---: | :---: | :---: |
| Claude Sonnet 5.5 (high effort) | 5 (pilot + 4-trial series) | **0** | 116 / 118 |

---

## Objective

Alien Signals is a high-performance reactive-signals core of the kind that underpins modern UI frameworks.
A dashboard previews an edit by reading derived values, then cancels the edit if validation fails. Batching
can't roll this back, because the reads have already changed caches, dependency edges and owned
subscriptions.

This is a **feature-implementation** task. The agent adds a synchronous public `transaction<T>(fn: () => T): T`
export in which **every nested transaction is a savepoint**. An escaping error aborts that frame, restores its
entry state exactly and rethrows the original value. A successful inner frame joins its parent, and an outer
abort undoes successful inner work. Upstream has batching but no transactions. The brief is
[`instruction.md`](instruction.md), and `INCIDENT.md` in the baseline holds the
incident report.

## Technical challenges

Rollback has to cover the **entire reactive bookkeeping**, not just signal values:

| Area | What must hold |
| --- | --- |
| Values & reads | Tentative writes visible inside the frame through chains, diamonds and conditional deps; exact result/error identity (including `undefined` and primitives) |
| Graph state | Restore cached values, previous-value getter state, *actual* dependency edges, pending work, force-trigger state and ambient tracking. A computed first evaluated in an aborted frame becomes unevaluated again |
| Publication | Publish only the final committed state. No observer runs for a net-unchanged outcome, even if speculative reads advanced a cache and returned it. An aborted `trigger` must not leak its forced notification |
| Batching | Compose with existing `startBatch`/`endBatch`, including work queued *before* the transaction. An abort may neither erase it nor flush it early |
| Resource ownership | Effects and scopes created in an aborted frame are released once, in reverse creation order, *after* state is restored. Stopping a pre-existing effect is tentative until outer commit. Computed re-evaluation that replaces owned children must restore the old generation on abort |
| Failure semantics | Commit-side observer and cleanup errors never cause rollback. Attempt all compensation and preserve the original error over secondary failures. Reentrant writes and transactions from callbacks must settle correctly |

The reference implementation is +266 / −25 lines across `src/system.ts` and `src/index.ts`. The difficulty lies in
the interaction of savepoints with lazy evaluation, dependency tracking and ownership, not in volume.

## Environment

| Item | Value |
| --- | --- |
| Agent image | `python:3.12.11-slim-bookworm` + Node (digest-pinned, multi-stage), `pnpm@9.12.0`, pinned `git`/`make`/`g++` |
| Baseline | Reconstructed from [`environment/base.bundle`](environment/base.bundle) at `995e375e…`, dependencies installed |
| Agent | Unprivileged `solver`, **no network**, 3000 s timeout, 2 CPU / 4 GB |
| Verifier | **Separate environment**, no network, 300 s timeout |

## Verification methodology

This task uses the shared Dusker SWE verifier design ([`../README.md`](../README.md#verifier-design)).
Only production source under `src/` is exported. The verifier builds it from scratch **as both CommonJS and ESM**
and runs every behavioural check against each build through a Node driver
([`tests/api_driver.cjs`](tests/api_driver.cjs)). Editing tests or generated files
cannot supply the feature.

- **S01–S28** are scenario groups (values, savepoints, batching, publication, tracking, ownership, compensation,
  reentrancy).
- **G01** is 24 generated reactive-graph scenarios (`model_graph_i_j`) whose expected traces come from a reference
  model. It acts as a differential test against hand-written expectations.
- **P01–P04** preserve non-transactional behaviour (batches, laziness, scope ownership, low-level system API).

## Validation controls (current task lineage)

| Control | Expected | Recorded | Record |
| --- | :---: | :---: | --- |
| Oracle (reference fix) | 1 | **1** | [`release-oracle`](jobs/validation/alien-signals-graph-savepoints/release-oracle/alien-signals-graph-savepoints__QJDrRPs/verifier/grade.json) |
| NOP (unchanged baseline) | 0 | **0** | [`release-nop`](jobs/validation/alien-signals-graph-savepoints/release-nop/alien-signals-graph-savepoints__MwJbcXK/verifier/grade.json) |
| Reference clean regrade | 1 | **1** | [`reference-clean-regrade`](jobs/validation/alien-signals-graph-savepoints/reference-clean-regrade/alien-signals-graph-savepoints__VAZPcdf/verifier/grade.json) |
| Integrity probe (adversarial agent) | 0 | **0** | [`probe.json`](jobs/validation/alien-signals-graph-savepoints/release-integrity/alien-signals-graph-savepoints__F4VPbiv/agent/probe.json) |

`jobs/validation/alien-signals-failure-propagation/` and `jobs/history/` retain runs from an earlier task in the
same lineage. They are kept for provenance only.

## Evaluation results

Claude Sonnet 5.5 via Claude Code 2.1.295, `reasoning_effort=high`, `max_turns=100`, 3000 s agent timeout,
no automatic retries, sequential trials, web tools and sub-agents disabled. A single-trial pilot was followed
by a four-trial series. Every trial was regraded in a clean environment with an identical result.

| Series | Trial | Result | Fail-to-pass | Pass-to-pass | Non-passing tests (CJS + ESM) | Turns | Agent time | Est. cost |
| --- | :---: | :---: | :---: | :---: | --- | :---: | :---: | :---: |
| `20261015T200144Z-331679` (pilot) | [1](jobs/trial-runs/alien-signals-graph-savepoints/20261015T200144Z-331679/jobs/trial-1/alien-signals-graph-savepoints__gQQgqUQ/verifier/grade.json) | Fail | 116/118 | 10/10 | `S22 caught_failed_initial_scope_releases_partial_work_at_commit` | 11 | 8.7 min | $1.10 |
| `20261015T201356Z-e8dd80` | [1](jobs/trial-runs/alien-signals-graph-savepoints/20261015T201356Z-e8dd80/jobs/trial-1/alien-signals-graph-savepoints__yFbn6y9/verifier/grade.json) | Fail | 116/118 | 10/10 | `S22 caught_failed_initial_scope_releases_partial_work_at_commit` | 13 | 10.3 min | $1.27 |
| `20261015T201356Z-e8dd80` | [2](jobs/trial-runs/alien-signals-graph-savepoints/20261015T201356Z-e8dd80/jobs/trial-2/alien-signals-graph-savepoints__c63kU4g/verifier/grade.json) | Fail | 114/118 | 10/10 | `S18 cleanup_failure_precedes_observer_failure_after_commit`; `S22 …` | 32 | 11.2 min | $1.61 |
| `20261015T201356Z-e8dd80` | [3](jobs/trial-runs/alien-signals-graph-savepoints/20261015T201356Z-e8dd80/jobs/trial-3/alien-signals-graph-savepoints__Lk6rfQ8/verifier/grade.json) | Fail | 116/118 | 10/10 | `S22 caught_failed_initial_scope_releases_partial_work_at_commit` | 14 | 11.8 min | $1.46 |
| `20261015T201356Z-e8dd80` | [4](jobs/trial-runs/alien-signals-graph-savepoints/20261015T201356Z-e8dd80/jobs/trial-4/alien-signals-graph-savepoints__vHeDfxb/verifier/grade.json) | Fail | 116/118 | 10/10 | `S22 caught_failed_initial_scope_releases_partial_work_at_commit` | 32 | 13.1 min | $1.92 |

Costs are client-side estimates; provider billing was not independently measured.

### Observed failure mode

All five trials implemented the transaction API well enough to pass 114–116 of 118 behavioural checks,
including all 24 model-generated graph scenarios. Every trial failed the same **ownership edge case**
(S22). A scope is created inside a transaction, its initial callback throws, and the parent transaction
catches the error and then commits. The partially created resources must be released at commit. The
scenario combines rules the brief states separately: initial callbacks still run immediately, errors caught
inside a callback do not abort it, and deferred releases are completed at commit. One trial also failed S18,
which covers reporting when both a cleanup and an observer fail after commit (the brief requires attempting
all of them and reporting the first failure encountered). Both misses sit at the intersection of error
handling and resource ownership, the part of the specification that is hardest to keep consistent.

## Reproduce

From the [`swe-bench/`](../) directory, with Harbor and Docker:

```sh
env -u DOCKER_DEFAULT_PLATFORM harbor run -p alien-signals-graph-savepoints -a oracle -n 1 -k 1 -r 0 --jobs-dir runs/oracle
env -u DOCKER_DEFAULT_PLATFORM harbor run -p alien-signals-graph-savepoints -a nop    -n 1 -k 1 -r 0 --jobs-dir runs/nop
```

## Contents

```text
alien-signals-graph-savepoints/
├── instruction.md · task.toml
├── environment/                 Pinned task environment
├── solution/                    Reference fix
├── tests/                       Verifier and test fixtures
├── README.md
├── jobs/                        Preserved evaluation evidence
└── provenance.json              Original source catalog and packaging location
```

Upstream licence: [`../licenses/alien-signals-graph-savepoints-LICENSE.txt`](../licenses/alien-signals-graph-savepoints-LICENSE.txt).
