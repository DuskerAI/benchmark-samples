# Claude Opus 4.8 — Terminal Task Evaluation (pass@4)

**Model:** Claude Opus 4.8 (`claude-opus-4-8`)
**Harness:** Claude Code (native agent), Docker
**Sample:** 10 shared tasks · **Design:** pass@4 — 4 attempts per task

## Results

| # | Task | Attempts | Passes | Solve rate | Solved (pass@4) |
|---|------|:---:|:---:|:---:|:---:|
| 01 | db-ecommerce-analytics | 4 | 2 | 0.50 | Yes |
| 02 | job-shop-scheduling | 4 | 2 | 0.50 | Yes |
| 03 | sbom-vulnerability-analyzer | 4 | 3 | 0.75 | Yes |
| 04 | ffmpeg-video-pipeline | 4 | 1 | 0.25 | Yes |
| 05 | lang-go-merkle-tree | 4 | 1 | 0.25 | Yes |
| 06 | numeric-dashboard-fullstack-repair | 4 | 3 | 0.75 | Yes |
| 07 | corpus-ranking-repair | 4 | 3 | 0.75 | Yes |
| 08 | tiered-cache-optimizer | 4 | 4 | 1.00 | Yes |
| 09 | event-sourcing-compactor | 4 | 4 | 1.00 | Yes |
| 10 | debug-terraform-aws-infra | 4 | 1 | 0.25 | Yes |

## Summary

- **pass@4: 10 / 10 tasks solved**
- **Mean solve rate across tasks: 0.60**
- **Score spread across tasks: 0.25 – 1.00**
- **Attempts completed: 40 / 40**

## Run configuration

- **Model:** claude-opus-4-8
- **Harness:** native Claude Code agent, run in Docker
- **Attempts per task:** 4 (pass@4)
- **Timeouts:** task and verifier at 2× base; agent-setup at 3× base
- **Reasoning effort:** high (`--effort high`) on the three re-run attempts (tasks 04, 06, 07); the remaining attempts used the harness default. A fully uniform high-effort re-run of all 10 tasks is available on request.

Per-task data is also provided in `results.csv`.

## Verification

These results are independently verifiable — every attempt ships with its full agent transcript
(recording `claude-opus-4-8` and Anthropic request IDs), the actual test output that determined
pass/fail, and the harness records with timestamps and cost. See **`evidence/HOW_TO_VERIFY.md`**
and **`evidence/provenance.csv`** (40 trials, indexed to request IDs).
