# Dusker AI — Benchmark Samples

Dusker AI designs evaluation tasks for AI coding agents. Each task has a reproducible containerised
environment, an automated verifier hidden from the agent during evaluation, a reference solution, and recorded
runs against frontier models.

This package contains 130 tasks: nine original samples with packaged evaluation records and 121 additional
custom Terminal-Bench-style tasks without packaged model scores. The nine evaluated samples below were
developed independently by Dusker AI following the Terminal-Bench,
SWE-bench and TB 4.0 task methodologies. They are not part of the official benchmark suites.

- **[Terminal-Bench](Terminal-Bench/):** Terminal-Bench-style agentic terminal tasks. The agent works in a purpose-built Docker
  environment to build, debug or repair a running system, and the verifier grades the resulting state.
- **[SWE-bench](swe-bench/):** SWE-bench-style repository-level tasks on pinned open-source codebases. The
  agent's patch is rebuilt and graded in an isolated verifier against fail-to-pass and pass-to-pass tests that
  are hidden from the agent.
- **[TB 4.0](TB-4.0/):** a long-horizon systems task following the TB 4.0 methodology, repairing a Rust
  consensus service under deterministic fault injection.
- **[Dusker AI expert bench](terminal-bench-v2/):** 121 additional custom tasks, with a website catalog;
  not verified as members of the official Terminal-Bench 2.0 or 2.1 datasets.

## Tasks at a glance

| Suite | # | Task | Domain | Stack | Verifier | Recorded results |
| --- | :---: | --- | --- | --- | :---: | --- |
| Terminal-Bench | 01 | [SBOM vulnerability & compliance analyzer](Terminal-Bench/Task-01/) | Supply-chain security | Go · CycloneDX · CVSS 3.1 | 88 tests | Opus 4.8 **3/4** · GPT-5.5 **0/4** |
| Terminal-Bench | 02 | [E-commerce analytics pipeline](Terminal-Bench/Task-02/) | Data engineering | Go · DuckDB · SQL window functions | 92 tests | Opus 4.8 **2/4** · GPT-5.5 **0/4** |
| Terminal-Bench | 03 | [Event-sourcing compactor repair](Terminal-Bench/Task-03/) | Distributed systems | TypeScript · PostgreSQL 16 | 23 tests + rubric | Opus 4.8 **4/4** · GPT-5.5 **4/4** |
| Terminal-Bench | 04 | [Full-stack numeric dashboard repair](Terminal-Bench/Task-04/) | Multi-language debugging | C/CMake · Express · React/Vite | 33 tests, 3 milestones | Opus 4.8 **3/4** · GPT-5.5 **3/3**† |
| SWE-bench | 01 | [LuaJIT: virtual heap identity across JIT exits](swe-bench/luajit-virtual-heap-exits/) | JIT compilers | C | 38 F2P / 10 P2P | Sonnet 5.5 **0/4** |
| SWE-bench | 02 | [go-workflows: provenance across nested resets](swe-bench/go-workflows-reset-provenance/) | Durable workflow engines | Go · SQLite | 28 F2P / 19 P2P | Sonnet 5.5 **0/4** |
| SWE-bench | 03 | [Alien Signals: transactional savepoints](swe-bench/alien-signals-graph-savepoints/) | Reactive dataflow | TypeScript (CJS + ESM) | 118 F2P / 10 P2P | Sonnet 5.5 **0/5** |
| SWE-bench | 04 | [SQLGlot: native window-function execution](swe-bench/sqlglot-window-visibility/) | Query engines | Python | 104 F2P / 22 P2P | Sonnet 5.5 **3/4** |
| TB 4.0 | 01 | [Raft membership repair](TB-4.0/) | Distributed consensus | Rust | 109 test cases (release 3.1.0) | GPT-6.1-sol **5/8** |

Results come from Dusker AI's own evaluation runs on these tasks and are not official leaderboard scores. Cells
show passing attempts / scored attempts, with binary reward (every verifier test must pass). Agent
harnesses: Claude Code for Claude models, OpenAI Codex for GPT-5.5, and Terminus-2 for GPT-6.1-sol.
† One GPT-5.5 attempt was not scored because the API usage limit was reached.

## Selection

The eight Terminal-Bench and SWE-bench tasks were chosen from 20 (ten per collection) on six criteria: technical depth, originality,
real-world relevance, environment quality, verifier robustness and observed difficulty. Together they span
six languages (C, Go, TypeScript, Python, JavaScript, SQL), eight engineering domains, both **repair** and
**feature-implementation** work, and an observed pass-rate range from 0/4 to 4/4. Terminal-Bench Task 03 does
not separate the two models. It is included for its environment and verifier design: a live service with a
real database and black-box concurrency tests.

## How the tasks are built

| Practice | Terminal-Bench | SWE-bench |
| --- | --- | --- |
| Reproducible environment | Version- or digest-pinned Docker images; offline-capable toolchains pre-cached at build time | Digest-pinned images; baseline shipped as a Git bundle and rebuilt at a fixed commit |
| Hidden grading | `tests/` and `solution/` are hidden from the agent during evaluation | Hidden test patch applied only in a **separate, network-isolated verifier** after the candidate is captured |
| Ground truth | Independently recomputed (e.g., DuckDB SQL over raw inputs) or specification-derived values | Fail-to-pass / pass-to-pass inventories mapped to named requirement groups |
| Anti-shortcut measures | Input checksums, hard-coding heuristics, rebuild-and-restart before testing | Trusted rebuild from exported production sources only; asset fingerprints before and after; sandboxed native execution |
| Controls | Reference solution included for Oracle runs | Oracle, NOP, clean regrade and an **adversarial integrity probe**, all recorded |
| Evidence | Full agent transcripts, raw verifier output, harness records | Transcripts, captured candidate source, build/integrity logs, grades, clean regrades |

## Evaluation summary

| Suite | Model | Attempts passed | Tasks solved (any pass) |
| --- | --- | :---: | :---: |
| Terminal-Bench (4 tasks) | Claude Opus 4.8 | 12 / 16 | 4 / 4 |
| Terminal-Bench (4 tasks) | GPT-5.5 | 7 / 15 scored | 2 / 4 |
| SWE-bench (4 tasks) | Claude Sonnet 5.5 | 3 / 17 | 1 / 4 |
| TB 4.0 (1 task) | GPT-6.1-sol | 5 / 8 | 1 / 1 |

Model failures here are rarely wholesale. Most failed attempts satisfy all but one or two requirements:
96–99% of fail-to-pass tests in every failed SWE-bench trial, and all but 1–3 tests in most failed
Terminal-Bench attempts. The verifiers are exhaustive enough to catch a single missed edge case, such as a
side-trace variant in LuaJIT, interior lineage corruption in go-workflows, or an ownership case in Alien
Signals. Each task README records the exact failing tests and links to the raw output.

Trial counts are small (4–8 per model per task) and describe observed outcomes rather than statistical
rankings. Run settings for each model are listed in the
[Terminal-Bench evaluation protocol](Terminal-Bench/README.md#evaluation-protocol).

## Layout

```text
Dusker_AI_Benchmark_Samples/
├── README.md                    This overview
├── LICENSE                      Dusker AI proprietary notice
├── verify_samples.py            Offline integrity check for the whole package
├── Terminal-Bench/
│   ├── README.md                Suite overview, task format, evaluation protocol
│   ├── task-packages.sha256     Hashes of every task-package file (recorded at packaging)
│   ├── evaluation-records/      Run-level records for both models
│   └── Task-01 … Task-04/       README.md · <task-id>/ (runnable task) · evaluation/ (all attempts)
├── swe-bench/
│   ├── README.md                Suite overview, verifier design, evaluation protocol
│   ├── NOTICE.md                Upstream licence notices
│   ├── licenses/ · docs/ · tools/
│   └── <task-slug>/            Runnable task · README.md · jobs/ (evidence) · provenance.json
└── TB-4.0/
    ├── README.md                Task overview and results
    ├── raft-membership-repair/  Task package
    └── raft-difficulty-v3.1-…/  Evaluation run (8 attempts)
```

## Verifying this package

```sh
python3 -B verify_samples.py            # manifests, SWE source hashes, catalog paths; no task execution
python3 -B verify_samples.py --bundles  # also verifies bundled Git repository structure
```

The script checks the supplied checksum manifests, original SWE source-file hashes and website catalog
paths. Optional bundle checks verify Git bundle structure without executing task code. It does not
rerun controls, validate reported model scores, apply patches or check every documentation link.

See [SECURITY.md](SECURITY.md) for the publication review and its limits. The existing proprietary
license is unchanged; public availability does not grant an open-source license.

## Reproducing a task

Requires Docker and [Harbor](https://github.com/harbor-framework/harbor). Each task README gives exact
commands. A typical control run:

```sh
cd SWE-bench
env -u DOCKER_DEFAULT_PLATFORM harbor run -p sqlglot-window-visibility -a oracle -n 1 -k 1 -r 0 --jobs-dir runs/oracle
```

## Licensing

© 2026 Dusker AI. All rights reserved. The tasks, verifiers, reference solutions and documentation in this
package are proprietary to Dusker AI ([LICENSE](LICENSE)). Bundled upstream open-source projects keep their own
licences ([NOTICE](swe-bench/NOTICE.md)).

## Additional task collection and named paths

The additional [Dusker AI Terminal-Bench-style collection](terminal-bench-v2/) contains
121 custom tasks. These are not included in the nine-task evaluation summary above.
Task directories use their actual slugs rather than numbered wrappers:

- `swe-bench/luajit-virtual-heap-exits/`
- `swe-bench/go-workflows-reset-provenance/`
- `swe-bench/alien-signals-graph-savepoints/`
- `swe-bench/sqlglot-window-visibility/`
- `terminal-bench-v2/ada-btrfs-recovery-auditor/` (and 120 other tasks)

Website inventories: [swe-bench/catalog.json](swe-bench/catalog.json) and
[terminal-bench-v2/catalog.json](terminal-bench-v2/catalog.json). The Terminal-Bench-style
collection has no established official 2.0/2.1 dataset membership or packaged model scores.
