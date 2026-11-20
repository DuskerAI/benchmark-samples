Complete `/app/src`. Binary `/app/target/release/tomo_repair` takes `<scan_dir> <output_json>`; other arg counts fail with stderr `usage:`. Handle same-schema scans; honor output path; deterministic; no input writes.

Output JSON with `width`, `height`, `pixel_spacing`, `origin`, `values`, `summary`. Use geometry dimensions/spacing. `values` is row-major, finite, non-negative, length `width * height`. `summary` is an object with finite `min`, `max`, `mean`, `active_mean`, `active_energy`, `measurement_residual`.

Apply `calibration.json` before reconstruction/residuals. Strings `detector_gain: already_applied` and `dark_current: already_removed` mean no change. Numeric scalars use `(raw - dark_current) / detector_gain`. Numeric arrays are per-detector: length `detector_count`; angle-major ray `i` uses `i % detector_count`. `angle_unit` is degrees.

`measurement_residual` is relative L2 of calibrated measurements vs nearest-neighbor diagnostic projection. Per ray: `half = max(width, height) * pixel_spacing * 0.76`; sample `sample_count` points from `-half` to `half` inclusive; round grid coordinates; add in-bounds value times ray step. Match within 1.5e-6.

Quality uses active-mask MAE/RMSE. Leakage is inactive/active energy. Contrast is high-ref mean minus low-ref active mean.

|Case|max|uniq|resid|MAE|RMSE|leak|contrast|hi|lo|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|`/app/data`|0.90|120|0.080|0.095|0.155|0.035|0.70|0.80|0.05|
|perturbed|0.70|100|0.085|0.075|0.130|0.035|0.62|0.72|0.04|
|cal-pert|0.70|100|0.085|0.075|0.130|0.035|0.62|0.72|0.04|
|stress|1.10|180|0.025|0.068|0.110|0.004|0.78|0.90|0.04|
|cal-stress|1.10|180|0.025|0.068|0.110|0.004|0.78|0.90|0.04|
