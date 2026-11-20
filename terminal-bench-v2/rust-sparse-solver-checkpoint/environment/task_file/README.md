# spsolve job contract

Run the solver as:

```text
cargo run --locked -- run <job-file> <report-file>
```

The job file is UTF-8 text. Blank lines and lines whose first non-space character is `#` are ignored. Each case begins with `case <name>` and ends with `end`. Inside a case, `matrix <path>` and `rhs <path>` are required; `checkpoint <path>` is optional. Paths may be absolute or relative to the job file's directory. Case names are unique and contain no whitespace.

Matrix files use MatrixMarket coordinate real format with `general` or `symmetric` storage. Indices are 1-based, comment lines may appear before the size line, and repeated coordinates contribute additively. Symmetric storage supplies one triangle; off-diagonal entries represent both mirrored positions. The right-hand side file contains one real value per non-comment line and its length must match the matrix dimension.

A checkpoint file may be created or updated by a run and may be supplied to later jobs. It is an acceleration cache only: loading it must not change the mathematical result. Cached numeric data may be used only for the exact same numeric matrix; a matrix with the same sparsity pattern but different values must be solved as a new numeric system.

The report file is JSON with this shape:

```json
{"cases":[{"name":"case-name","n":3,"solution":[0.0,0.0,0.0],"residual_norm":0.0,"certified":true,"used_checkpoint":false}]}
```

Cases must appear in job order. `solution` values are IEEE double precision decimal numbers. `residual_norm` is the infinity norm of `A*x-b` computed for the original input system. `certified` is true only when that norm is at most `1e-8 * (1 + ||b||_inf)`. Invalid inputs or uncertified solves must exit non-zero and leave a report that is either absent or incomplete.
