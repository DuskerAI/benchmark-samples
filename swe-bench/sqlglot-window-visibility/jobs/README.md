# Evaluation records: sqlglot-window-visibility

[Task package](../) · [Job index](index.json)

This directory retains 4 model trials and 16 native trial results,
including validation controls and available clean regrades. Both successful and
failed model runs are preserved. A NOP reward of zero is the expected control outcome.

`validation/` contains saved control runs. `trial-runs/` contains model series where
retained; some datasets also include earlier validation revisions under `history/`.
Use `index.json` for portable paths. Native configurations, transcripts, submitted
source, and grades are retained from execution time.

A `*.reference.json` file represents a byte-identical capture retained in the same
dataset. From the repository root, verify or materialize the missing directory:

```sh
python3 -B tools/archive.py verify PATH_TO_MISSING_DIRECTORY
python3 -B tools/archive.py restore PATH_TO_MISSING_DIRECTORY
```

See [evaluation methodology](../../docs/evaluation.md) for interpretation,
control requirements, and the distinction between acceptance and infrastructure failures.
