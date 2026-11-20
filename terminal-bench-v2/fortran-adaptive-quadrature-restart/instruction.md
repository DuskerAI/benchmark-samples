Scientific-computing work is in `/app/quad`: build with `make` and make `./quad_solver <config> <output.csv> <checkpoint>` produce deterministic adaptive Simpson estimates for `integral_0^1 x^alpha*(1-x)^beta*cos(omega*x) dx`.

Config files use `key=value` entries for `alpha`, `beta`, `omega`, `tol`, `checkpoint_interval`, `panel_limit`, and `restart`. Valid cases have `alpha` and `beta` greater than `-0.5`, nonnegative `omega`, `1e-10 <= tol <= 1e-5`, and nonnegative integer limits. Invalid cases must exit nonzero without creating an output CSV.

Completed runs must write exactly `result,error_estimate,panels` as the CSV header plus one finite row, with a nonnegative error estimate and positive panel count. If `panel_limit > 0`, the run may stop after writing a restartable checkpoint and exit code `3`; rerunning with `restart=true` and `panel_limit=0` from that checkpoint must produce byte-identical CSV to the same case run without interruption.
