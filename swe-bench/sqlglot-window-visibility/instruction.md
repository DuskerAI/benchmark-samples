# Preserve row identity and visibility when executing SQL windows

The offline report preview uses SQLGlot's native Python executor. Queries accepted
by the parser and optimizer fail when they contain `OVER`; removing `OVER` changes
the report's cardinality. `incident/window_visibility.py` reproduces the incident,
and `tests/test_window_preview.py` contains small public examples.

Implement native window execution through the existing `execute` API and the
`Plan` / `PythonExecutor` path. Use the existing expression evaluator and its
function environment; the engine must remain standard-library-only and must not
delegate execution to SQLite, DuckDB, or another SQL engine.

The required fragment uses finite numeric, text, and NULL scalar values in SELECT
queries, including WHERE, grouped inputs and HAVING, derived tables, CTEs, and
inner joins. Support the following semantics together:

- Windows preserve every surviving input row and its multiplicity. Multiple
  windows with different partition/order specifications read the same relation
  and attach results to the correct rows. NULL partition keys belong together;
  peers compare the complete ordering tuple, with ASC/DESC and NULLS FIRST/LAST.
- Support SUM, COUNT (including `*`), AVG, MIN and MAX, scalar operands, and
  aggregate FILTER predicates with SQL NULL/empty-frame behavior. Support
  ROW_NUMBER, RANK, DENSE_RANK, PERCENT_RANK, CUME_DIST, NTILE, LAG, LEAD,
  FIRST_VALUE, LAST_VALUE, and NTH_VALUE, respecting NULL values. LAG/LEAD defaults
  apply only outside the partition; ranking and LAG/LEAD ignore frame exclusion.
- Support ROWS, RANGE, and GROUPS with CURRENT ROW, UNBOUNDED endpoints and
  non-negative literal offsets; inclusive boundaries and clipping can yield empty
  frames. Numeric RANGE offsets require one ordering term and respect its
  direction; a non-numeric current RANGE key uses its peers. Default ordered
  frames include peers through the current group; an unordered default covers
  the partition. Support EXCLUDE CURRENT ROW, GROUP, TIES, and NO OTHERS.
- Resolve named and inherited windows. Finish grouping and HAVING before window
  evaluation, including aggregates used as window operands or ordering keys.
  Evaluate QUALIFY after windows and before DISTINCT, final ORDER BY, LIMIT and
  OFFSET. Windows may appear in SELECT, QUALIFY, and final ORDER BY; intermediate
  columns must not appear in the returned schema.
- Preserve caller-owned ASTs and registered table data, and return the same
  result on repeated execution of a plan. Direct PythonExecutor calls must honor
  its custom scalar and aggregate function environment inside window evaluation.
- Reject invalid offsets (negative, fractional ROWS/GROUPS or navigation offsets,
  zero NTH_VALUE/NTILE), offset RANGE with zero/multiple ordering terms, and
  DISTINCT window aggregates with ExecuteError. DISTINCT window aggregates,
  IGNORE NULLS, interval frames, recursive queries, and windows in set-operation
  branches are outside the required fragment.

Use valid SQL frame endpoint combinations. Do not impose a new tie-breaking rule
on SQL expressions that leave row order unspecified. Preserve existing supported
non-window behavior. Add relevant tests to the repository; the hidden verifier
evaluates behavior independently of those tests.
