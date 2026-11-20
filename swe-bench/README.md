# SWE-bench-Style Repository Samples

Four repository-level software-engineering tasks from **Dusker SWE**, Dusker AI's 10-task collection.
Each task pins a real codebase at a fixed commit, describes an incident or feature request in prose, and
grades the agent's patch with an isolated behavioural verifier. The format follows SWE-bench
(fail-to-pass / pass-to-pass tests against a pinned baseline). Each task adds a separate verifier
environment, a trusted rebuild of the submitted source, and recorded validation controls.

| # | Task | Upstream project | Language | Type | F2P / P2P | Sonnet 5.5 |
| --- | --- | --- | --- | --- | :---: | :---: |
| 01 | [LuaJIT virtual heap identity across JIT exits](luajit-virtual-heap-exits/) | LuaJIT | C | Repair | 38 / 10 | 0/4 |
| 02 | [go-workflows history & effect provenance across resets](go-workflows-reset-provenance/) | go-workflows | Go | Repair | 28 / 19 | 0/4 |
| 03 | [Alien Signals nested transactional savepoints](alien-signals-graph-savepoints/) | alien-signals | TypeScript | Feature | 118 / 10 | 0/5 |
| 04 | [SQLGlot native window-function execution](sqlglot-window-visibility/) | sqlglot | Python | Feature | 104 / 22 | 3/4 |

Model results are Claude Sonnet 5.5 at high reasoning effort, the only model evaluated on this suite. Task 03
includes a one-trial pilot plus a four-trial series. These are small samples that describe observed outcomes;
they are not statistical difficulty rankings.

## Task format

Each `Task-0N/` folder mirrors one `datasetN/` directory of the source repository, so the relative paths
inside the evidence remain valid:

```text
Task-0N/
├── <task-slug>/                 Harbor task (schema 1.4) — byte-identical to the source repository
│   ├── instruction.md           Incident / feature brief shown to the agent
│   ├── task.toml                Artifacts exported to the verifier, network policy, limits, pinned commits
│   ├── environment/             Agent image: Dockerfile + base.bundle (Git bundle of the pinned baseline)
│   ├── tests/                   Verifier image: Dockerfile, base.bundle, parser.py, config.json,
│   │                            test_patch.patch (hidden tests), run_script.sh, test.sh, language driver
│   └── solution/                gold_patch.diff + solve.sh (reference fix, used by the Oracle control)
├── jobs/                        Evaluation evidence (index.json = portable navigation)
├── provenance.json              Source catalog entry: commits, per-file SHA-256, controls, test counts
└── README.md
```

Baselines ship as Git bundles. Repository history and upstream notices stay inside the bundle, and both
agent and verifier images rebuild the same commit offline.

## Verifier design

All four tasks share one hardened grading pipeline ([`tests/test.sh`](luajit-virtual-heap-exits/tests/test.sh)
and `tests/parser.py` in each task):

1. **Separate verifier environment.** The agent's container is never graded in place. Harbor exports only the
   artifact paths declared in `task.toml` (production source roots) into a fresh verifier image built from the
   same pinned baseline. Agent and verifier both run with **no network**.
2. **Trusted rebuild.** The verifier confirms the baseline commit and fingerprints its own assets (`protect`).
   It then rebuilds the candidate from the exported sources only (`build`). Submitted tests, generated files and
   binaries are ignored, and oversize or non-regular files are rejected.
3. **Hidden tests applied late.** `test_patch.patch` is applied only inside the verifier, after the candidate
   is captured. A second fingerprint (`integrity`) confirms no trusted asset changed.
4. **Exact-inventory grading.** `config.json` lists every fail-to-pass and pass-to-pass test and maps them to
   named **requirement groups**. A run passes only if the executed test set exactly equals that inventory and
   every test passes. Missing, extra, errored or skipped tests are classified as harness/execution errors, kept
   separate from genuine acceptance failures. The grade records per-group results.
5. **Least privilege.** Agents run as an unprivileged user. Where the candidate is native code, execution is
   additionally sandboxed (chroot, UID drop, `no_new_privs`, CPU/file/process rlimits).

### Validation controls

Every task carries recorded controls for its current release lineage, linked from each task README and
from `provenance.json`:

| Control | What it shows | Expected reward |
| --- | --- | :---: |
| **Oracle** | The reference fix passes the verifier | 1 |
| **NOP** | The unmodified baseline fails, on the intended behavioural assertions and without infrastructure errors | 0 |
| **Reference clean regrade** | A fresh rebuild plus reference fix passes, independent of cached containers or prior workspaces | 1 |
| **Integrity probe** | An adversarial agent that tries to read hidden tests and solutions, forge `reward.txt` / `grade.json`, edit tests and plant generated files still scores 0 | 0 |

All 16 current-lineage controls in this sample (4 per task) recorded the expected reward. Every model trial
was also regraded in a clean environment, and all 17 regrades reproduced their original grade.

## Evaluation protocol

| Setting | Value |
| --- | --- |
| Model | `anthropic/claude-sonnet-5-5` (observed in every trajectory) |
| Agent | Claude Code 2.1.295 (Harbor `claude-code`), CLI binary checksum recorded in each `series.json` |
| Effort / turns | `reasoning_effort=high`, `max_turns=100` |
| Tools | `WebFetch`, `WebSearch`, `Agent`, `Task` disabled; agent network disabled |
| Timeouts | 3000 s agent; verifier 300–600 s per task |
| Retries / concurrency | No automatic retries; trials run sequentially |
| Regrading | Every trial regraded in a clean verifier environment |
| Platform | Linux ARM64 Docker (agent binary `linux-arm64`); collection checked with Harbor 0.22.0 |

Per-trial records in `jobs/trial-runs/<slug>/<series>/` include the full Claude Code transcript
(`agent/trajectory.json`), the captured candidate source, build and integrity logs, the grade (`verifier/grade.json`)
and the clean regrade. Cost figures are client-side estimates recorded by the agent; provider billing was not
independently measured.

## Reproducing

Requires Docker and [Harbor](https://github.com/harbor-framework/harbor) (checked with 0.22.0 on Linux ARM64).
From this directory:

```sh
env -u DOCKER_DEFAULT_PLATFORM harbor run -p sqlglot-window-visibility -a oracle -n 1 -k 1 -r 0 --jobs-dir runs/oracle
env -u DOCKER_DEFAULT_PLATFORM harbor run -p sqlglot-window-visibility -a nop    -n 1 -k 1 -r 0 --jobs-dir runs/nop
```

Docker builds fetch pinned base images and packages. Task execution then follows each task's declared network
policy, so use a provider that enforces it. Do not mount a whole task folder into an agent container: it contains
the reference fix and the hidden tests. Detailed guidance is in [`docs/evaluation.md`](docs/evaluation.md).

Some captures are stored once and referenced elsewhere as `*.reference.json`. They can be verified or
materialised from this directory with the bundled tool:

```sh
python3 -B tools/archive.py verify  <path-to-missing-directory>
python3 -B tools/archive.py restore <path-to-missing-directory>
```

Run [`../verify_samples.py --bundles`](../verify_samples.py) for an offline check of package hashes, evidence
hashes, controls, archive references, baseline reconstruction and patch applicability.

## Licensing

Dusker AI's original benchmark material is proprietary ([LICENSE](../LICENSE)). Bundled upstream projects keep
their own licences; see [NOTICE.md](NOTICE.md).
