# go-workflows: History & Effect Provenance Across Nested Resets

**Suite:** SWE-bench-style repository task · **Task ID:** `dusker-ai/go-workflows-reset-provenance` v3.0.0 · **Category:** durable workflow engines · **Language:** Go
**Upstream:** [cschleiden/go-workflows](https://github.com/cschleiden/go-workflows) @ `48e81194` · **Baseline commit:** `8290e0a5` · **Tests:** 28 fail-to-pass + 19 pass-to-pass in 20 requirement groups

| Model | Trials | Passed | Fail-to-pass (best) |
| --- | :---: | :---: | :---: |
| Claude Sonnet 5.5 (high effort) | 4 | **0** | 27 / 28 |

---

## Objective

go-workflows is a durable-execution engine in the style of Temporal or Cadence. Workflows are replayed
from an event history, and side effects (activities, timers, child workflows) are scheduled as commands. The
baseline adds a SQLite **reset** prototype that restarts a workflow from an earlier committed task boundary
*without repeating external side effects*. A simple reset works, but:

- **nested resets lose history**;
- **completed activities can run again**;
- a **late result from an abandoned execution can satisfy a different command** after the reset reuses its
  numeric schedule ID.

The agent must repair the reset boundary so the executor and backend agree on the selected history **and on
the external work that history owns**. The full recovery contract is in `backend/sqlite/RESET.md`, the
incident in `INCIDENT-RESET.md`, and a public reproduction in `backend/sqlite/incident_reset_test.go`
(all in the baseline repository). The brief is [`instruction.md`](instruction.md).

## Technical challenges

The contract has five interlocking parts, each exercised by its own requirement groups:

1. **Shared history across arbitrary nested resets.** Preserve event identity, attributes, logical sequence and
   suffix reads, including cuts before an inherited boundary. Detect invalid cuts and corrupt ancestry: missing,
   cyclic, interior, gapped or duplicated lineage.
2. **Effect provenance.** Adopt retained outstanding activity, timer and child publications. Keep leases and child
   parent identities. Reuse already accepted results without reissuing work. Ensure results from *abandoned*
   publications can never satisfy new work that reuses a command slot.
3. **Post-cut external inputs.** Replay consumed and pending signals and accepted results in order. Restore the
   original timer delivery after a post-cut cancellation without a second logical firing.
4. **Atomicity and fencing.** Reset atomically, make retries write-free, keep stale workflow acquisitions fenced,
   and reject invalid requests with no partial changes.
5. **Retention.** Keep ancestry needed by live branches, fail safely when a referenced source is removed, and
   reclaim eligible leaves without stranding newer executions or resurrecting removed ones.

All of this must preserve public signatures, the event wire format, queues, payload converters and warm
execution. The reference fix is concentrated in `backend/sqlite/reset.go` (+126 / −36) but depends on a precise
model of the whole executor/backend contract.

## Environment

| Item | Value |
| --- | --- |
| Agent image | `golang:1.25.3-bookworm` (digest-pinned); Go module cache and build cache pre-warmed |
| Baseline | Reconstructed from [`environment/base.bundle`](environment/base.bundle) at `8290e0a5…` |
| Storage | Embedded SQLite (no external services) |
| Agent | Unprivileged `solver`, **no network**, 3000 s timeout, 2 CPU / 4 GB |
| Verifier | **Separate environment**, no network, 360 s timeout |

## Verification methodology

This task uses the shared Dusker SWE verifier design ([`../README.md`](../README.md#verifier-design)).
Production sources from 12 runtime package roots (`backend/`, `workflow/`, `internal/`, `client/`, …) are
exported, rebuilt against the trusted baseline, and driven by a Go test driver
([`tests/driver.go`](tests/driver.go)) under a Python harness. Requirement
groups B01–B14 cover the behaviours to repair. P01–P06 pin the preserved behaviour: argument validation,
ownership, cold and warm execution, signal states, acknowledgement and retention.

## Validation controls (current task lineage)

| Control | Expected | Recorded | Record |
| --- | :---: | :---: | --- |
| Oracle (reference fix) | 1 | **1** | [`release-oracle-final`](jobs/validation/go-workflows-reset-provenance/release-oracle-final/go-workflows-reset-provenance__9eYyVcz/verifier/grade.json) |
| NOP (unchanged baseline) | 0 | **0** | [`release-nop-final`](jobs/validation/go-workflows-reset-provenance/release-nop-final/go-workflows-reset-provenance__8sVshyP/verifier/grade.json) |
| Reference clean regrade | 1 | **1** | [`reference-clean-regrade-final`](jobs/validation/go-workflows-reset-provenance/reference-clean-regrade-final/go-workflows-reset-provenance__8gVd4EB/verifier/grade.json) |
| Integrity probe (adversarial agent) | 0 | **0** | [`probe.json`](jobs/validation/go-workflows-reset-provenance/release-integrity-final/go-workflows-reset-provenance__528kwQV/agent/probe.json) |

`jobs/validation/` also retains runs from two earlier tasks in this task's development lineage
(`go-workflows-replay-turns`, `go-workflows-checkpoint-fencing`). They are kept for provenance only and are not
part of the reported results.

## Evaluation results

Claude Sonnet 5.5 via Claude Code 2.1.295, `reasoning_effort=high`, `max_turns=100`, 3000 s agent timeout,
no automatic retries, sequential trials, web tools and sub-agents disabled. Every trial was regraded in a
clean environment with an identical result.

| Series | Trial | Result | Fail-to-pass | Pass-to-pass | Non-passing tests | Turns | Agent time | Est. cost |
| --- | :---: | :---: | :---: | :---: | --- | :---: | :---: | :---: |
| `20261016T094726Z-0d3106` | [1](jobs/trial-runs/go-workflows-reset-provenance/20261016T094726Z-0d3106/jobs/trial-1/go-workflows-reset-provenance__YGBejWZ/verifier/grade.json) | Fail | 27/28 | 19/19 | `test_B12_interior` | 44 | 17.2 min | $2.87 |
| `20261016T094726Z-0d3106` | [2](jobs/trial-runs/go-workflows-reset-provenance/20261016T094726Z-0d3106/jobs/trial-2/go-workflows-reset-provenance__xUAbaTU/verifier/grade.json) | Fail | 27/28 | 19/19 | `test_B12_interior` | 45 | 14.4 min | $2.49 |
| `20261016T094726Z-0d3106` | [3](jobs/trial-runs/go-workflows-reset-provenance/20261016T094726Z-0d3106/jobs/trial-3/go-workflows-reset-provenance__ZkAs5YP/verifier/grade.json) | Fail | 27/28 | 19/19 | `test_B12_interior` | 43 | 16.9 min | $2.60 |
| `20261016T094726Z-0d3106` | [4](jobs/trial-runs/go-workflows-reset-provenance/20261016T094726Z-0d3106/jobs/trial-4/go-workflows-reset-provenance__ry6B97K/verifier/grade.json) | Fail | 27/28 | 19/19 | `test_B12_interior` | 50 | 22.0 min | $3.20 |

Costs are client-side estimates; provider billing was not independently measured. Series metadata:
[`series.json`](jobs/trial-runs/go-workflows-reset-provenance/20261016T094726Z-0d3106/series.json).

### Observed failure mode

Every trial delivered the core behaviour: nested-reset history sharing, effect adoption, abandoned-result
fencing, signal and timer recovery, atomicity and retention. All four failed the same ancestry-validation case.
B12 rejects corrupt reset lineage in five forms (missing, cycle, gap, duplicate, interior), and every trial
handled four of them but accepted an **interior** corruption. The brief requires that the backend "detect
invalid cuts and incomplete or corrupt ancestry". The consistent miss shows how easily defensive validation of
persisted lineage is overlooked once the happy path works.

## Reproduce

From the [`swe-bench/`](../) directory, with Harbor and Docker:

```sh
env -u DOCKER_DEFAULT_PLATFORM harbor run -p go-workflows-reset-provenance -a oracle -n 1 -k 1 -r 0 --jobs-dir runs/oracle
env -u DOCKER_DEFAULT_PLATFORM harbor run -p go-workflows-reset-provenance -a nop    -n 1 -k 1 -r 0 --jobs-dir runs/nop
```

## Contents

```text
go-workflows-reset-provenance/
├── instruction.md · task.toml
├── environment/                 Pinned task environment
├── solution/                    Reference fix
├── tests/                       Verifier and test fixtures
├── README.md
├── jobs/                        Preserved evaluation evidence
└── provenance.json              Original source catalog and packaging location
```

Upstream licence: [`../licenses/go-workflows-reset-provenance-LICENSE.txt`](../licenses/go-workflows-reset-provenance-LICENSE.txt).
