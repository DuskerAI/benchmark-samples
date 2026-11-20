# Run-level evaluation records

Run-level records for the two pass@4 evaluations.

```text
gpt-5.5/
├── README.md · RESULTS.md · results.csv      Original summary of the GPT-5.5 run
└── evidence/
    ├── HOW_TO_VERIFY.md · provenance.csv     One provenance row per attempt (40)
    └── run/{config.json, result.json, job.log}
claude-opus-4.8/
├── RESULTS.md · results.csv                  Original summary of the Opus 4.8 run
└── evidence/
    ├── HOW_TO_VERIFY.md · provenance.csv     One row per attempt (40), with sample request IDs
    └── runs/<job>/{config.json, result.json, job.log}   7 jobs
```

## Locating attempt directories

The original `HOW_TO_VERIFY.md` files refer to attempt directories as `run/<trial_id>/…` (GPT-5.5)
and `runs/<job>/<trial_id>/…` (Opus 4.8). In this sample they live with their task:

| Original path | Location in this sample |
| --- | --- |
| `gpt-5.5/evidence/run/<trial_id>/` | `../Task-0N/evaluation/gpt-5.5/<trial_id>/` |
| `claude-opus-4.8/evidence/runs/<job>/<trial_id>/` | `../Task-0N/evaluation/claude-opus-4.8/<job>/<trial_id>/` |

Only attempts for the four sampled tasks are included. The summaries, provenance files and harness
`result.json` files still cover all ten tasks of the full run.

## Notes

1. Per-job run settings (reasoning effort, timeout multipliers) are recorded in each job's `config.json`
   and summarised in the [suite evaluation protocol](../README.md#evaluation-protocol).
2. Two GPT-5.5 attempts reached the API usage limit. They are marked `QUOTA_ERROR` in `provenance.csv`
   and are not scored.
