# TB 4.0 — Raft Membership Repair (Rust)

**Task ID:** `terminal-bench/raft-membership-repair` · **Category:** software / systems · **Language:** Rust
**Expert estimate:** 20 h · **Agent time limit:** 8 h · **Verifier:** separate environment, deterministic fault-injection harness

| Model | Agent | Attempts | Passed | pass@4 |
| --- | --- | :---: | :---: | :---: |
| GPT-6.1-sol (`openai/gpt-6.1-sol`, high reasoning effort) | Terminus-2 | 8 | **5** | 1.00 |

---

## Objective

`membershipd` is a Rust control-plane service that replicates tenant counters and conditional updates using
Raft-style consensus. A fleet-replacement rollout and a migration to asynchronous storage and application
workers exposed correctness failures:

- isolated replicas answered reads;
- retrying clients received inconsistent results after recovery;
- clusters stalled or disagreed after voter replacement.

The agent repairs the consensus and asynchronous-worker integration in `src/engine.rs` to satisfy the service
contract in [`CONTRACT.md`](raft-membership-repair/environment/project/CONTRACT.md). The brief is
[`instruction.md`](raft-membership-repair/instruction.md).

## Technical challenges

| Area | What must hold |
| --- | --- |
| Durability | A staged disk image is not durable. Storage and application callbacks must be processed, their independent completion frontiers preserved across overlapping work, and acknowledgements and compaction held until the right frontier is reached |
| Liveness under stalls | A stalled storage or application worker must not freeze consensus event processing. A connected, eligible quorum must eventually serve requests |
| Client semantics | Committed data and retry results survive leader changes, partitions, reordered or duplicated messages, restarts and log compaction. Reads are linearizable without appending log entries |
| Reconfiguration | Joint-consensus membership changes stay safe while replacing several voters, including the leader, and complete after leadership changes. Learners never elect a leader |
| Snapshots | Snapshot install preserves client sessions and a matching uncommitted suffix, and stale snapshots cannot roll back committed state |

Declining every request or retaining every log entry does not satisfy the contract.

## Environment

| Item | Value |
| --- | --- |
| Image | `rust:1.85.1-slim-bookworm` (digest-pinned) + Python 3; crate dependencies pre-built and cached for offline builds |
| Project | [`environment/project/`](raft-membership-repair/environment/project/): `src/engine.rs` (consensus module), `src/types.rs`, `src/main.rs` (deterministic transport/storage adapter), `CONTRACT.md`, `public_checks.py` (public harness and incident reproducer) |
| Resources | 4 CPUs · 4 GB RAM · 10 GB storage |
| Timeouts | agent 28,800 s · verifier 900 s · build 1,800 s |

## Verification methodology

- **Single collected artifact.** Only `src/engine.rs` is collected. The verifier compiles it against pristine copies
  of the adapter, type definitions, Cargo manifests and build files in a separate environment.
- **Sandboxed replicas.** Each replica process starts through a seccomp-filtered launcher
  ([`boundary.c`](raft-membership-repair/tests/pristine/boundary.c)). Consensus code gets stdin/stdout/stderr and
  memory, clock and signal syscalls, but no ambient filesystem or peer channels. All I/O goes through the
  deterministic adapter.
- **Independent oracle.** [`tests/cluster.py`](raft-membership-repair/tests/cluster.py) drives a deterministic
  transport and checks results against a replicated-state-machine specification written independently of any Rust
  implementation.
- **Fault schedules.** Crash cuts, partitions, reordered and duplicated messages, worker stalls, membership
  rollouts and compaction, plus parametrized generated operational histories.

The test suites in this folder ([`test_consensus.py`](raft-membership-repair/tests/test_consensus.py),
[`test_async.py`](raft-membership-repair/tests/test_async.py)) define 27 test functions, several of them
parametrized. Reward is binary.

## Evaluation results

The run in [`raft-difficulty-v3.1-20261023-135007/`](raft-difficulty-v3.1-20261023-135007/) evaluated release
3.1.0 of this task. That release extends the version in this folder with multi-version transactions and streamed
snapshot transfer (`mvcc.rs`, `transfer.rs`). Its verifier runs 56 test functions (109 cases) across five suites:
consensus 29, async 19, transactions 26, transfer 23, interleavings 12. Those include the 27 test functions here.

Run configuration: Harbor 0.22.0, Docker, Terminus-2 agent, `reasoning_effort=high`, 8 attempts run concurrently,
no retries.

| Trial | Reward | Tests passed | Failing tests | Agent time | Cost |
| --- | :---: | :---: | --- | :---: | :---: |
| [`6Phoe98`](raft-difficulty-v3.1-20261023-135007/raft-membership-repair__6Phoe98/verifier/test-stdout.txt) | 1 | 109 / 109 | — | 47.8 min | $1.23 |
| [`A3JddNn`](raft-difficulty-v3.1-20261023-135007/raft-membership-repair__A3JddNn/verifier/test-stdout.txt) | 1 | 109 / 109 | — | 24.4 min | $0.97 |
| [`XuYyHWp`](raft-difficulty-v3.1-20261023-135007/raft-membership-repair__XuYyHWp/verifier/test-stdout.txt) | 1 | 109 / 109 | — | 29.8 min | $1.23 |
| [`bfRn6Yh`](raft-difficulty-v3.1-20261023-135007/raft-membership-repair__bfRn6Yh/verifier/test-stdout.txt) | 1 | 109 / 109 | — | 32.8 min | $1.39 |
| [`hdBWay3`](raft-difficulty-v3.1-20261023-135007/raft-membership-repair__hdBWay3/verifier/test-stdout.txt) | 1 | 109 / 109 | — | 33.0 min | $1.34 |
| [`AsqMfon`](raft-difficulty-v3.1-20261023-135007/raft-membership-repair__AsqMfon/verifier/test-stdout.txt) | 0 | 107 / 109 | `test_each_compaction_request_completes_when_its_prefix_is_eligible[1]`, `[3]` | 25.0 min | $1.07 |
| [`jwq7wqQ`](raft-difficulty-v3.1-20261023-135007/raft-membership-repair__jwq7wqQ/verifier/test-stdout.txt) | 0 | 107 / 109 | `test_each_compaction_request_completes_when_its_prefix_is_eligible[1]`, `[3]` | 37.7 min | $0.72 |
| [`mpHyCQM`](raft-difficulty-v3.1-20261023-135007/raft-membership-repair__mpHyCQM/verifier/test-stdout.txt) | 0 | 107 / 109 | `test_each_compaction_request_completes_when_its_prefix_is_eligible[1]`, `[3]` | 31.8 min | $1.23 |

**Summary:** 5 / 8 passed (mean reward 0.625; pass@2 0.89, pass@4 1.00). Total cost $9.18 (18.3 M input,
0.50 M output tokens), from the harness [`result.json`](raft-difficulty-v3.1-20261023-135007/result.json).

### Observed failure mode

All three failing attempts passed every consensus, durability, transaction and transfer check, and failed the same
two interleaving cases. In those cases an eligible compaction request waited behind an **unrelated later
application job** (`eligible compaction waits for an unrelated later application job`). The contract requires each
compaction request to complete once its own prefix is eligible, which means tracking completion frontiers per
request rather than serializing behind the application queue.

## Reproduce

```sh
harbor run -p raft-membership-repair -a oracle   # reference solution: solution/engine.rs
harbor run -p raft-membership-repair -a nop      # unmodified service
```

## Contents

```text
TB-4.0/
├── raft-membership-repair/                    Task package
│   ├── instruction.md · task.toml
│   ├── environment/                           Dockerfile + project/ (Rust service, CONTRACT.md, public_checks.py)
│   ├── solution/                              solve.sh + engine.rs (reference solution)
│   └── tests/                                 Verifier image, cluster.py, test_consensus.py, test_async.py, pristine/ build inputs
└── raft-difficulty-v3.1-20261023-135007/      Evaluation run (8 attempts)
    ├── config.json · lock.json · result.json · job.log
    └── raft-membership-repair__<id>/          agent/trajectory.json · artifacts/ · verifier/{test-stdout.txt, ctrf.json, reward.txt}
```
