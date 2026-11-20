# Terminal-Bench Samples

Four agentic terminal tasks from Dusker AI's 10-task Terminal-Bench-style collection. Each task drops an
agent into a purpose-built Docker environment with a natural-language brief. The agent must build,
debug or repair software from the shell, and an automated verifier then grades the resulting system
state.

| # | Task | Domain | Stack | Tests | Opus 4.8 | GPT-5.5 |
| --- | --- | --- | --- | :---: | :---: | :---: |
| 01 | [SBOM vulnerability & compliance analyzer](Task-01/) | Supply-chain security | Go · CycloneDX · CVSS 3.1 | 88 | 3/4 | 0/4 |
| 02 | [E-commerce analytics pipeline](Task-02/) | Data engineering | Go · DuckDB · SQL windows | 92 | 2/4 | 0/4 |
| 03 | [Event-sourcing compactor repair](Task-03/) | Distributed systems | TypeScript · PostgreSQL 16 | 23 | 4/4 | 4/4 |
| 04 | [Full-stack numeric dashboard repair](Task-04/) | Multi-language debugging | C/CMake · Express · React/Vite | 33 | 3/4 | 3/3† |

Cells show passes / scored attempts at pass@4. † One GPT-5.5 attempt was not scored because the API
usage limit was reached.

## Task format

Tasks follow the Harbor / Terminal-Bench task layout (`task.toml` schema `version = "2.0"`):

```text
<task-id>/
├── instruction.md      Brief shown to the agent
├── task.toml           Metadata (difficulty, category, time estimates) and agent/verifier/build limits
├── environment/        Dockerfile (+ compose file / entrypoint where needed) and task_file/ inputs
├── solution/           Reference solution (solve.sh; milestone tasks add solve1.sh … solveN.sh)
└── tests/              test.sh entry point + pytest verifier; writes /logs/verifier/reward.txt (0 or 1)
```

During evaluation, the agent never sees `solution/` or `tests/`. Rewards are binary: every verifier test must pass.

## Evaluation protocol

Both models were evaluated at **pass@4** on the same 10-task suite, inside Docker, through the Harbor
harness with each vendor's native coding agent. The settings below come from the retained job
configurations in [`evaluation-records/`](evaluation-records/).

| | GPT-5.5 | Claude Opus 4.8 |
| --- | --- | --- |
| Agent harness | OpenAI Codex (Harbor `codex`) | Claude Code (Harbor `claude-code`) |
| Model string | `gpt-5.5` | `claude-opus-4-8` |
| Attempts | 4 per task, one job | 4 per task, assembled from 7 jobs |
| Reasoning effort | `high` for all attempts | Harness default; `high` for the top-up job (one attempt each on tasks 04, 06, 07 of the full suite) |
| Timeout multiplier | 2.0× task/verifier, 3.0× agent setup | 1.0× in six jobs; 2.0× / 3.0× in the top-up job |
| Unscored attempts | 2 of 40 (API usage limit) | None |

GPT-5.5 ran with high effort and doubled timeouts throughout; most Opus 4.8 attempts ran at default
effort and base timeouts. The per-task tables state the configuration of each attempt.

### Full-suite context

Summary of both runs across the complete 10-task collection, for context. Tasks in this sample
are in **bold**.

| Source ID | Task | Opus 4.8 passes | GPT-5.5 passes / valid |
| --- | --- | :---: | :---: |
| **01** | **db-ecommerce-analytics** | 2/4 | 0/4 |
| 02 | job-shop-scheduling | 2/4 | 1/3 |
| **03** | **sbom-vulnerability-analyzer** | 3/4 | 0/4 |
| 04 | ffmpeg-video-pipeline | 1/4 | 0/4 |
| 05 | lang-go-merkle-tree | 1/4 | 0/4 |
| **06** | **numeric-dashboard-fullstack-repair** | 3/4 | 3/3 |
| 07 | corpus-ranking-repair | 3/4 | 4/4 |
| 08 | tiered-cache-optimizer | 4/4 | 4/4 |
| **09** | **event-sourcing-compactor** | 4/4 | 4/4 |
| 10 | debug-terraform-aws-infra | 1/4 | 3/4 |
| | **Tasks solved at pass@4** | **10/10** | **6/10** |

Sources: [`claude-opus-4.8/RESULTS.md`](evaluation-records/claude-opus-4.8/RESULTS.md),
[`gpt-5.5/RESULTS.md`](evaluation-records/gpt-5.5/RESULTS.md).

## Evidence

Each task folder contains every attempt for that task from both models under `evaluation/`. Each
attempt includes:

- `agent/trajectory.json`: the full agent transcript, with model identifiers and token usage
  (Opus 4.8 transcripts also carry Anthropic request IDs);
- `verifier/test-stdout.txt`: the raw pytest output that determined the result;
- `verifier/reward.txt`: the recorded reward (0/1).

Run-level records (job configs, harness `result.json`, `job.log`, provenance CSVs and the original
summaries) are in [`evaluation-records/`](evaluation-records/). See that folder's
README for notes on reading them.

## Reproducing

Requires Docker and [Harbor](https://github.com/harbor-framework/harbor). From a task folder:

```sh
harbor run -p <task-id> -a oracle   # reference solution; expected reward 1
harbor run -p <task-id> -a nop      # unmodified environment; expected reward 0
```

Notes:

- Environment builds download pinned toolchains (Go, Node, Postgres images) and need network access.
- Several verifiers install their test runner at grading time (`pip install pytest…` for Tasks 01–02;
  `uv` from `astral.sh` for Tasks 03–04), so verification also needs network access.
- Task 01 pins `--platform=linux/amd64`. On ARM hosts it runs under emulation.

The integrity of every file in this folder can be checked offline with
[`../verify_samples.py`](../verify_samples.py). Task-package hashes are recorded in
[`task-packages.sha256`](task-packages.sha256).
