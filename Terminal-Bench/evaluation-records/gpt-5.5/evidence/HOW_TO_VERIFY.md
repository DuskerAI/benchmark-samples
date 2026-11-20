# How to verify the GPT-5.5 results are real

Every GPT-5.5 number comes from a real OpenAI Codex run against `gpt-5.5`. This folder
lets you confirm it independently — same standard as the Opus 4.8 evidence.

## 30-second spot check

1. Open `provenance.csv` — one row per trial: task, outcome (pass(1) / fail(0) / QUOTA_ERROR),
   model (`gpt-5.5`), and the token count of the trial's last API call.
2. Open `run/<trial_id>/verifier/test-stdout.txt` — the **actual test output** that decided
   pass/fail, with `reward.txt` (0/1) next to it.
3. Open `run/<trial_id>/agent/trajectory.json` — the **full Codex transcript**. It records
   `"model":"gpt-5.5"` and real token usage (`total_tokens`) for each API call.

## Proof signals

| Signal | Where | Proves |
|---|---|---|
| Model string | `"model":"gpt-5.5"` in every `trajectory.json` / `codex.txt` | Calls really went to gpt-5.5 |
| Exact command | `run/job.log` — `codex … --model gpt-5.5 -c model_reasoning_effort=high` | Correct model **and** high reasoning effort |
| Token usage | `total_tokens` per call in each `trajectory.json`; totals in `run/result.json` | Real generation happened (14.2M input tokens, $65 billed) |
| Verifier output | `run/<trial>/verifier/test-stdout.txt` + `reward.txt` | Pass/fail set by the task's own tests, not asserted |
| Harness records | `run/result.json` | Timestamps, per-trial rewards, token counts, cost — machine-generated |
| Quota errors | 2 trials marked `QUOTA_ERROR` (HTTP 429) | You cannot be billing-rate-limited without real API calls |

## Reproduce it

`run/config.json` is the Harbor job config. With Harbor + Docker and an OpenAI key:
`harbor run -c config.json` re-runs the same tasks against gpt-5.5.

## Full disclosure

- **38 of 40 trials are valid.** Two (`02-…KCuQbtx`, `06-…7vv624c`) hit an OpenAI billing-quota
  error (HTTP 429) and are excluded from pass counts. Both are on tasks already solved by other
  attempts, so the 6/10 result does not depend on them.
- Effort was pinned to `high` via Codex `-c model_reasoning_effort=high`.
