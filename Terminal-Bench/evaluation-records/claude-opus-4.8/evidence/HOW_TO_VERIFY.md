# How to verify these results are real

Every number in `RESULTS.md` comes from a real Harbor trial run against Claude Opus 4.8.
This folder lets you confirm that independently.

## 30-second spot check

1. Open `provenance.csv`. Pick any row — it lists the task, the reward (1 = pass, 0 = fail),
   the model (`claude-opus-4-8`), and a sample Anthropic **request ID** (`req_…`).
2. Open the matching `runs/<source_job>/<trial_id>/verifier/test-stdout.txt`.
   This is the **actual test output** (pytest / shell) that decided pass or fail —
   e.g. `1 failed, 57 passed`. The `reward.txt` next to it is the 0/1 the harness recorded.
3. Open `runs/<source_job>/<trial_id>/agent/trajectory.json`.
   This is the **full agent transcript**. Search it for `claude-opus-4-8` (the model) and
   `req_` (per-request IDs). Those request IDs are server-side records at Anthropic and can be
   confirmed as genuine Opus 4.8 API calls.

## The four independent proof signals

| Signal | Where | What it proves |
|---|---|---|
| Model + request IDs | every `trajectory.json`; indexed in `provenance.csv` | The calls were really made to `claude-opus-4-8` (37/40 trials list a `req_…` ID; all 40 transcripts are included) |
| Verifier output | `runs/**/verifier/test-stdout.txt` + `reward.txt` | The pass/fail was produced by the task's own tests, not asserted by us |
| Harness records | `runs/<job>/result.json` | UTC timestamps, per-trial rewards, token counts, and cost — machine-generated and internally consistent |
| Exact commands | `runs/<job>/job.log` | The real invocation, including the model and (for the top-up job) `--effort high` |

## Reproduce it yourself

Each `runs/<job>/config.json` is the Harbor job config. With Harbor + Docker:
`harbor run -c config.json` re-runs the same tasks against the same model.

## Folder map

```
provenance.csv                 40 trials → task, reward, model, request ID
runs/<job>/result.json         harness results (timestamps, rewards, tokens, cost)
runs/<job>/job.log             exact commands run
runs/<job>/config.json         reproducible job config
runs/<job>/<trial>/verifier/   test-stdout.txt (real test output) + reward.txt (0/1)
runs/<job>/<trial>/agent/      trajectory.json (full Opus 4.8 transcript)
```

## Full-disclosure notes

- The 40 counted attempts span several job runs (the columns in `provenance.csv` show which).
  Two earlier runs were discarded as invalid and are **not** included or counted: one where the
  harness errored on every trial, and one where the agent never actually started.
- 3 of the 40 trials (all failures) have only a minimal agent log because the agent errored early;
  one of those (`10-…__6hFHAQA`) failed on a network/DNS error during setup, not on the task itself.
  Their verifier output is still included.
- Reasoning effort was set to `high` on the three re-run attempts (tasks 04, 06, 07 in
  `pass4-topup-040607-high-effort`); the other attempts used the harness default.
