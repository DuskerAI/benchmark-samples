Repair `/app/task_file` so `riskcalc evaluate <portfolio-file>` accepts valid portfolios and rejects invalid input non-zero.

Input is UTF-8; strip blank lines and any `#...` suffix. Records: `portfolio <name>`, `scale <n>`, `factor <factor> <bucket> [<variance-lo>,<variance-hi>]`, `position <id> <factor> <amount> [<sensitivity-lo>,<sensitivity-hi>]`, `cov <factor-a> <factor-b> [<covariance-lo>,<covariance-hi>]`.

One `portfolio` then one `scale` precede numeric records. `scale` is 0..6. Names match `[A-Za-z][A-Za-z0-9_:-]{0,39}`. Factors and position ids are unique. References must exist. Covariance pairs are unordered; later duplicates replace earlier ones; self-covariance is invalid.

Decimals are optional sign plus `digits[.digits]` or `.digits`; reject bare sign/dot, exponent notation, and non-ASCII digits. Fractional length is unbounded. Intervals are closed `[lo,hi]`. Convert amounts, endpoints, products, and sums to portfolio-scale signed 128-bit ticks. Round lows down, highs up, scalars to the smallest containing interval. Use no floating point. Reject bad intervals or overflow; never wrap.

Add `amount * sensitivity` to each factor exposure. Bucket exposure sums same-bucket factors. Bucket variance sums `exposure_i * exposure_j * coefficient` for same-bucket pairs `i <= j`; diagonal coefficient is factor variance, off-diagonal coefficient is unordered covariance or zero, and off-diagonal terms count twice. Cross-bucket covariances are ignored for variance.

Stdout JSON keys/shape: `portfolio`, `scale`, `buckets` entries with `bucket`, `exposure`, `variance`, intervals as `{"lo":"...","hi":"..."}`, and `total` with `exposure` and `variance`. Order buckets by bytewise ascending name. Print exactly `scale` fractional digits, never negative zero, deterministic bytes.
