# LuaJIT: Virtual Heap Identity Across JIT Exits

**Suite:** SWE-bench-style repository task · **Task ID:** `dusker-ai/luajit-virtual-heap-exits` v1.0.0 · **Category:** JIT compiler deoptimization · **Language:** C (LuaJIT VM)
**Upstream:** [LuaJIT/LuaJIT](https://github.com/LuaJIT/LuaJIT) @ `2a8e017e` · **Baseline commit:** `620a6171` · **Tests:** 38 fail-to-pass + 10 pass-to-pass in 14 requirement groups

| Model | Trials | Passed | Fail-to-pass (best) |
| --- | :---: | :---: | :---: |
| Claude Sonnet 5.5 (high effort) | 4 | **0** | 37 / 38 |

---

## Objective

The baseline is a pinned LuaJIT revision extended with an experimental **table-graph allocation-sinking**
optimisation. The trace compiler keeps whole graphs of fresh, non-escaping tables "virtual", meaning
they are never heap-allocated, and only materialises them when a trace exits. The extension is
incorrect at those exits:

- shared child tables become *distinct* tables after an ordinary guard exit (identity lost);
- cycles, nested-only values and hot **side traces** fail more severely;
- escaping graphs behave differently depending on the order of their edge stores.

The agent must repair graph recovery across the **optimizer, snapshot machinery and side-trace boundary**.
A recovered graph must give each virtual allocation exactly one identity, restore exactly the selected
snapshot's values, and preserve aliases, cycles, array/hash fields, template copies and metatables.

The optimisation itself must survive. Non-escaping constant-key graphs must still run in native loops
**without heap allocations growing with iteration count**. Disabling JIT/sinking, eager allocation or
suppressing side traces is explicitly ruled out. The full brief is in
[`instruction.md`](instruction.md). The repository also carries
`incident/README.md` (the supported contract) and `incident/graph-exit.lua` (a public reproduction).

## Technical challenges

- **Compiler-internals reasoning.** The fix touches LuaJIT's SSA IR sink pass (`lj_opt_sink.c`) and
  snapshot restore (`lj_snap.c`). That requires understanding how sunk allocations and their store chains
  map to snapshot entries, and how exit handling reconstructs heap objects. That includes nodes reachable
  only through other virtual tables, which have no snapshot stack slot of their own.
- **Per-exit recovery state.** Recovery must belong to one exit, not to a later execution that reuses the
  same IR references. This is a temporal-aliasing bug class that is invisible in single-exit tests.
- **Cold vs. hot paths.** Each behaviour is tested twice: once via interpreter recovery (cold exit) and once
  after the exit becomes hot and a side trace is compiled and imports the virtual graph.
- **Two competing obligations.** Correctness (identity, values) and performance (allocation sinking still
  effective) must both hold. The verifier measures allocator requests as well as values.
- **Scale.** The reference fix is 2 files, +241 / −22 lines in a dense, macro-heavy C codebase.

## Environment

| Item | Value |
| --- | --- |
| Agent image | `golang:1.25.3-bookworm` (digest-pinned; provides GCC 12, make, Python 3) |
| Baseline | Reconstructed from [`environment/base.bundle`](environment/base.bundle) at `620a6171…`, built with `make BUILDMODE=static XCFLAGS=-DLUA_USE_ASSERT` |
| Supported platform | **Linux/aarch64** (the evaluation used ARM64 Docker) |
| Agent | Runs as unprivileged `solver`, **no network**, 3000 s timeout, 2 CPU / 4 GB |
| Verifier | **Separate environment**, no network, 600 s timeout |

## Verification methodology

This task uses the shared Dusker SWE verifier design ([`../README.md`](../README.md#verifier-design)).
Task-specific points:

- **Exported surface.** Only production sources under `src/` and `dynasm/` (`.c .h .lua .dasc .s .S`) are
  exported to the verifier. Generated files (`lj_bcdef.h`, `lj_vm.S`, …), submitted tests and binaries are
  ignored and rebuilt.
- **Sandboxed execution.** Each candidate VM run goes through
  [`launch_candidate.py`](tests/launch_candidate.py). It enters a `chroot`, drops
  to UID 1000, sets `no_new_privs`, and applies `RLIMIT_CPU` 5 s, `RLIMIT_FSIZE` 4 MiB and `RLIMIT_NPROC` 128.
- **Requirement groups.** G01–G11 cover graph shapes (diamond, deep, wide, pair, ring, self-cycle), hidden and
  pressure scenarios, distinct objects, ordering, FFI/payload/template graphs, mutation, upvalues, trace flush
  and coroutine/error paths, **each in cold and hot form**. P01–P03 assert that the optimisation is preserved
  (no allocation growth for sinkable graphs, real escapes still allocate).

## Validation controls (current task lineage)

| Control | Expected | Recorded | Record |
| --- | :---: | :---: | --- |
| Oracle (reference fix) | 1 | **1** | [`release-oracle-final-r3`](jobs/validation/luajit-virtual-heap-exits/release-oracle-final-r3/luajit-virtual-heap-exits__FGmskjZ/verifier/grade.json) |
| NOP (unchanged baseline) | 0 | **0** | [`release-nop-final-r3`](jobs/validation/luajit-virtual-heap-exits/release-nop-final-r3/luajit-virtual-heap-exits__fttp8BG/verifier/grade.json) |
| Reference clean regrade (fresh rebuild) | 1 | **1** | [`reference-clean-regrade-final-r3`](jobs/validation/luajit-virtual-heap-exits/reference-clean-regrade-final-r3/luajit-virtual-heap-exits__VixZJFk/verifier/grade.json) |
| Integrity probe (adversarial agent) | 0 | **0** | [`probe.json`](jobs/validation/luajit-virtual-heap-exits/release-integrity-probe-final-r3/luajit-virtual-heap-exits__ugSEFcD/agent/probe.json) |

The integrity probe tries to read hidden assets (all inaccessible) and forge `reward.txt` and `grade.json`.
It also plants a fake result and edits generated headers, an incident file and a `*_test.c` file. None of
this affects grading: the probe scores 0 F2P with all P2P passing.

## Evaluation results

Claude Sonnet 5.5 via Claude Code 2.1.295, `reasoning_effort=high`, `max_turns=100`, 3000 s agent timeout,
no automatic retries, trials run sequentially. Web tools and sub-agents were disabled. Every trial was
independently regraded in a clean environment with the same outcome.

| Series | Trial | Result | Fail-to-pass | Pass-to-pass | Non-passing tests | Turns | Agent time | Est. cost |
| --- | :---: | :---: | :---: | :---: | --- | :---: | :---: | :---: |
| `20261016T130255Z-a63e13` | [1](jobs/trial-runs/luajit-virtual-heap-exits/20261016T130255Z-a63e13/jobs/trial-1/luajit-virtual-heap-exits__JY4nEve/verifier/grade.json) | Fail | 37/38 | 10/10 | `test_G01_wide_hot` | 93 | 32.5 min | $3.75 |
| `20261016T130255Z-a63e13` | [2](jobs/trial-runs/luajit-virtual-heap-exits/20261016T130255Z-a63e13/jobs/trial-2/luajit-virtual-heap-exits__i7TWFc6/verifier/grade.json) | Fail | 37/38 | 10/10 | `test_G01_wide_hot` | 82 | 31.8 min | $3.93 |
| `20261016T130255Z-a63e13` | [3](jobs/trial-runs/luajit-virtual-heap-exits/20261016T130255Z-a63e13/jobs/trial-3/luajit-virtual-heap-exits__BSiDJPe/verifier/grade.json) | Fail | 37/38 | 10/10 | `test_G01_wide_hot` | 62 | 23.0 min | $2.75 |
| `20261016T130255Z-a63e13` | [4](jobs/trial-runs/luajit-virtual-heap-exits/20261016T130255Z-a63e13/jobs/trial-4/luajit-virtual-heap-exits__khjEAzE/verifier/grade.json) | Fail | 37/38 | 10/10 | `test_G01_wide_hot` | 76 | 25.9 min | $3.12 |

Costs are client-side estimates recorded by the agent; provider billing was not independently measured.
Series metadata: [`series.json`](jobs/trial-runs/luajit-virtual-heap-exits/20261016T130255Z-a63e13/series.json).

### Observed failure mode

All four trials repaired 37 of 38 fail-to-pass behaviours and preserved every pass-to-pass check, including
the optimisation-preservation groups. Each trial failed the same variant. G01 checks graph identity for
diamond, deep and wide shapes, each cold and hot, and every trial passed five of those six variants but
missed **wide-hot**: a wide fan-out graph recovered through a compiled side trace. The requirement is stated
directly in the brief ("Keep both interpreter recovery and hot side-trace compilation working"). The miss
reflects the difficulty of making side-trace import preserve identity for every shape, not a hidden
requirement.

## Reproduce

From the [`swe-bench/`](../) directory, with Harbor and Docker on an ARM64 host:

```sh
env -u DOCKER_DEFAULT_PLATFORM harbor run -p luajit-virtual-heap-exits -a oracle -n 1 -k 1 -r 0 --jobs-dir runs/oracle
env -u DOCKER_DEFAULT_PLATFORM harbor run -p luajit-virtual-heap-exits -a nop    -n 1 -k 1 -r 0 --jobs-dir runs/nop
```

## Contents

```text
luajit-virtual-heap-exits/
├── instruction.md · task.toml
├── environment/                 Pinned task environment
├── solution/                    Reference fix
├── tests/                       Verifier and test fixtures
├── README.md
├── jobs/                        Preserved evaluation evidence
└── provenance.json              Original source catalog and packaging location
```

Upstream licence: [`../licenses/luajit-virtual-heap-exits-COPYRIGHT.txt`](../licenses/luajit-virtual-heap-exits-COPYRIGHT.txt).
