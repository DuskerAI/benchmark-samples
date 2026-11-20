# Shallow-water restart contract

## Modes and arguments

The executable `/opt/swe/bin/shallow_water` accepts `--nx`, `--steps`, `--split`, `--seed`, `--prefix`, `--mode`, and `--checkpoint`. The supported modes are `pair`, `continuous`, `checkpoint`, and `resume`. The checkpoint path defaults to `<prefix>_restart.chk`.

`--split` is either one integer or a comma-separated nondecreasing list. Every listed boundary, including repeated boundaries, represents a checkpoint cycle: write the restart file, reload it through normal filesystem I/O, report the loaded state when applicable, then continue from the loaded state.

## Science output CSVs

Continuous and restarted outputs are written as `<prefix>_continuous.csv` or `<prefix>_restarted.csv` with this header:

```text
record,step,index,height,velocity,mass,momentum,energy
```

Each output CSV contains one summary row with `index`, `height`, and `velocity` equal to `0`, plus exactly `nx` cell rows with per-cell `height` and `velocity` and zeroed aggregate fields. Restarted, checkpointed, and paired segmented execution must match an uninterrupted run from the same state within `2e-11` absolute error for summary `mass`, `momentum`, and `energy`, and for every cell `height` and `velocity`.

## Checkpoint file format

The first checkpoint line has seven whitespace-separated numeric values, with no label row, in this order:

```text
nx step seed phase pulse spin drift
```

It is followed by exactly `nx` rows in this order:

```text
index height velocity retained lagged terrain stress tracer swirl
```

All checkpoint numbers must retain enough double-precision detail to preserve restart equivalence. Resume mode treats all header and row values as authoritative state rather than reconstructing them from CLI defaults.

## Paired restart audit CSV

Paired mode also writes `<prefix>_restart_audit.csv` with this header:

```text
segment,step,mass,momentum,energy,phase,pulse,spin,drift,memory_l2,lag_l2,terrain_l2,stress_l2,tracer_l2,swirl_l2
```

It contains one row after each requested checkpoint reload, including repeated split boundaries. Each row reports the state actually loaded from the restart file before any later time step is advanced.

## Rejection rules

Modes work across separate processes and create missing output and checkpoint parent directories. The program rejects unsupported modes, split lists that decrease, and restart files whose grid size, saved step, row count, or cell ordering contradicts the requested run. A restart checkpoint's saved step contradicts the requested run when the saved step is greater than the requested `--steps` value; resume mode must reject that checkpoint and must not write a restarted science CSV for it.
