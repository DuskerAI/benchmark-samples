# GPT-5.5 — Terminal Task Evaluation (pass@4)

**Model:** GPT-5.5 (`gpt-5.5`)
**Harness:** OpenAI Codex (native agent), Docker
**Sample:** 10 shared tasks · **Design:** pass@4 — 4 attempts per task · **Reasoning effort:** high

## Results

| # | Task | Passes | Valid attempts | Solved (pass@4) |
|---|------|:---:|:---:|:---:|
| 01 | db-ecommerce-analytics | 0 | 4 | No |
| 02 | job-shop-scheduling | 1 | 3 | Yes |
| 03 | sbom-vulnerability-analyzer | 0 | 4 | No |
| 04 | ffmpeg-video-pipeline | 0 | 4 | No |
| 05 | lang-go-merkle-tree | 0 | 4 | No |
| 06 | numeric-dashboard-fullstack-repair | 3 | 3 | Yes |
| 07 | corpus-ranking-repair | 4 | 4 | Yes |
| 08 | tiered-cache-optimizer | 4 | 4 | Yes |
| 09 | event-sourcing-compactor | 4 | 4 | Yes |
| 10 | debug-terraform-aws-infra | 3 | 4 | Yes |

## Summary

- **Tasks solved (pass@4): 6 / 10**
- **Pooled trial pass rate: 19 / 38 = 0.50**
- **Valid trials: 38 / 40**
- Score spread across tasks: 0.00 – 1.00

## Run configuration

- **Model:** gpt-5.5
- **Harness:** OpenAI Codex native agent, run in Docker
- **Attempts per task:** 4 (pass@4)
- **Reasoning effort:** high (Codex `-c model_reasoning_effort=high`)
- **Timeouts:** task and verifier at 2× base; agent-setup at 3× base

## Note on valid trials

Two of the 40 trials (one each on tasks 02 and 06) returned an OpenAI billing-quota error
(HTTP 429) — an account usage limit, not a model failure — and are excluded from the pass counts.
Both affected tasks were already solved by other attempts, so the 6/10 result is unaffected.

Per-task data is also in `results.csv`. Independently verifiable evidence (transcripts, test
output, harness records) is in `evidence/` — see `evidence/HOW_TO_VERIFY.md`.
