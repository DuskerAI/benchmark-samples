# GPT-5.5 Evaluation — Deliverable

Terminal-task evaluation of GPT-5.5 on a shared 10-task sample: pass@4, high reasoning effort,
native OpenAI Codex harness, Docker.

## Start here

- **`RESULTS.md`** — results table + summary.
- **`results.csv`** — per-task pass / valid / solved.

## Result in one line

**GPT-5.5 solved 6 / 10 tasks** (pass@4, high effort).

## Evidence (independently verifiable)

- **`evidence/`** — transcripts, verifier output, harness records, and `provenance.csv`
  (one row per trial). Nothing is hand-entered; every number traces to a real Codex run with
  its model string (`gpt-5.5`), token usage, and the task's own test output.
- See **`evidence/HOW_TO_VERIFY.md`** for a 30-second spot check.

## Note

38 of 40 trials are valid — 2 hit an OpenAI billing-quota error (not a model error), both on
already-solved tasks, so the 6/10 result is unaffected. Detail in `RESULTS.md` and the verify doc.
