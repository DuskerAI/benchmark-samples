# SQLGlot: Native SQL Window-Function Execution

**Suite:** SWE-bench-style repository task · **Task ID:** `dusker-ai/sqlglot-window-visibility` v1.0.0 · **Category:** query-engine semantic correctness · **Language:** Python
**Upstream:** [tobymao/sqlglot](https://github.com/tobymao/sqlglot) @ `ad8764a0` · **Baseline commit:** `0ebcabfc` · **Tests:** 104 fail-to-pass + 22 pass-to-pass in 19 requirement groups

| Model | Trials | Passed | Fail-to-pass (best) |
| --- | :---: | :---: | :---: |
| Claude Sonnet 5.5 (high effort) | 4 | **3** | 104 / 104 |

---

## Objective

SQLGlot is a widely used SQL parser, transpiler and optimiser, and it ships a pure-Python executor. In the
incident, an offline report preview uses that executor. Queries the parser and optimiser accept fail when
they contain `OVER`, and removing `OVER` changes the report's cardinality.

This is a **feature-implementation** task. The agent implements native window-function execution through the
existing `execute` API and the `Plan` / `PythonExecutor` path. It must reuse SQLGlot's own expression evaluator
and function environment, stay standard-library-only, and **must not delegate to SQLite, DuckDB or any other
engine**. The brief is [`instruction.md`](instruction.md). The baseline also contains
`incident/window_visibility.py` and public examples in `tests/test_window_preview.py`.

## Technical challenges

The required fragment is a substantial slice of the SQL standard's window semantics. All of it must work
together, over finite numeric, text and `NULL` values, through `WHERE`, `GROUP BY`/`HAVING`, derived tables,
CTEs and inner joins:

| Area | Requirements |
| --- | --- |
| Row identity | Preserve every surviving row and its multiplicity. Multiple windows with different partition/order specs attach to the right rows. `NULL` partition keys group together. Peers compare the full ordering tuple with ASC/DESC and NULLS FIRST/LAST |
| Functions | `SUM`, `COUNT(*)`, `AVG`, `MIN`, `MAX` with `FILTER`; `ROW_NUMBER`, `RANK`, `DENSE_RANK`, `PERCENT_RANK`, `CUME_DIST`, `NTILE`; `LAG`/`LEAD` (defaults only outside the partition); `FIRST_VALUE`, `LAST_VALUE`, `NTH_VALUE` |
| Frames | `ROWS`, `RANGE` and `GROUPS` with `CURRENT ROW`, `UNBOUNDED` and literal offsets; empty frames; numeric `RANGE` direction rules; peer-inclusive defaults; `EXCLUDE CURRENT ROW / GROUP / TIES / NO OTHERS` |
| Query phases | Named and inherited windows. Grouping and `HAVING` complete before windows (aggregates as window operands). `QUALIFY` runs after windows but before `DISTINCT`, `ORDER BY`, `LIMIT` and `OFFSET`. Intermediate columns stay out of the result schema |
| Engineering | Do not mutate caller-owned ASTs or registered tables. Repeat executions of a plan are deterministic. Custom function environments are honoured inside windows. Invalid frames and offsets raise `ExecuteError` |

The reference implementation adds a window module and planner/executor integration: +334 lines across
`sqlglot/planner.py`, `sqlglot/executor/python.py` and a new `sqlglot/executor/window.py`.

## Environment

| Item | Value |
| --- | --- |
| Agent image | `python:3.12.11-slim-bookworm` (digest-pinned), pinned `git` and pinned Python packages |
| Baseline | Reconstructed from [`environment/base.bundle`](environment/base.bundle) at `0ebcabfc…` |
| Agent | Unprivileged `solver`, **no network**, 3000 s timeout, 2 CPU / 4 GB |
| Verifier | **Separate environment**, no network, 300 s timeout, 2 CPU / 2 GB |

## Verification methodology

This task uses the shared Dusker SWE verifier design ([`../README.md`](../README.md#verifier-design)).
126 behavioural cases are grouped into W01–W18 (window semantics) and P01 (preserved non-window behaviour):

- **W01–W10** cover aggregates, row identity, `ROWS`/`GROUPS`/`RANGE` frames, exclusion, `FILTER`, ranking,
  navigation and value functions;
- **W11–W16** cover named windows, grouping interplay, `QUALIFY`, multi-stage pipelines (CTEs, derived tables,
  joins), empty inputs and plan reuse with custom environments;
- **W17** holds nine rejection cases (invalid offsets, multi-key `RANGE`, `DISTINCT` windows);
- **W18** runs **metamorphic tests** over deterministic seeded inputs, covering input identities, `NULL` peers
  and frame variants;
- **P01** includes 12 regression checks on existing executor features (three-valued `CASE`, `DISTINCT`, CTEs, …).

## Validation controls (current task lineage)

| Control | Expected | Recorded | Record |
| --- | :---: | :---: | --- |
| Oracle (reference fix) | 1 | **1** | [`release-oracle`](jobs/validation/sqlglot-window-visibility/release-oracle/sqlglot-window-visibility__gYmtcva/verifier/grade.json) |
| NOP (unchanged baseline) | 0 | **0** | [`release-nop`](jobs/validation/sqlglot-window-visibility/release-nop/sqlglot-window-visibility__5SHCBRJ/verifier/grade.json) |
| Reference clean regrade | 1 | **1** | [`reference-clean-regrade`](jobs/validation/sqlglot-window-visibility/reference-clean-regrade/sqlglot-window-visibility__LjP7hdB/verifier/grade.json) |
| Integrity probe (adversarial agent) | 0 | **0** | [`probe.json`](jobs/validation/sqlglot-window-visibility/release-integrity/sqlglot-window-visibility__ovaUfLy/agent/probe.json) |

`jobs/history/initial-validation/` keeps an earlier validation round of this task for provenance.

## Evaluation results

Claude Sonnet 5.5 via Claude Code 2.1.295, `reasoning_effort=high`, `max_turns=100`, 3000 s agent timeout,
no automatic retries, sequential trials, web tools and sub-agents disabled. Every trial was regraded in a
clean environment with an identical result.

| Series | Trial | Result | Fail-to-pass | Pass-to-pass | Non-passing tests | Turns | Agent time | Est. cost |
| --- | :---: | :---: | :---: | :---: | --- | :---: | :---: | :---: |
| `20261015T071646Z-1d6208` | [1](jobs/trial-runs/sqlglot-window-visibility/20261015T071646Z-1d6208/jobs/trial-1/sqlglot-window-visibility__wTA7wLn/verifier/grade.json) | **Pass** | 104/104 | 22/22 | — | 54 | 26.3 min | $3.62 |
| `20261015T071646Z-1d6208` | [2](jobs/trial-runs/sqlglot-window-visibility/20261015T071646Z-1d6208/jobs/trial-2/sqlglot-window-visibility__Hp3XQsY/verifier/grade.json) | **Pass** | 104/104 | 22/22 | — | 54 | 20.4 min | $3.02 |
| `20261015T071646Z-1d6208` | [3](jobs/trial-runs/sqlglot-window-visibility/20261015T071646Z-1d6208/jobs/trial-3/sqlglot-window-visibility__N6LaBKu/verifier/grade.json) | **Pass** | 104/104 | 22/22 | — | 73 | 28.4 min | $4.64 |
| `20261015T071646Z-1d6208` | [4](jobs/trial-runs/sqlglot-window-visibility/20261015T071646Z-1d6208/jobs/trial-4/sqlglot-window-visibility__DwVo2zg/verifier/grade.json) | Fail | 103/104 | 22/22 | `test_window_join_input` | 54 | 51.4 min | $4.75 |

Agent time is taken from harness timestamps. Costs are client-side estimates.

### Observed failure mode

This is the most tractable of the four SWE-bench samples. Three of four trials implemented the complete
window fragment, a substantial engine feature, in a single session. The failing trial missed one of five
multi-stage pipeline cases (W14): a window evaluated over a **join input**, where row identity must survive
the join before window attachment. The task shows the suite's calibration range. It is a demanding
specification that a strong model can complete, with the residual failures concentrated in phase and identity
interactions.

## Reproduce

From the [`swe-bench/`](../) directory, with Harbor and Docker:

```sh
env -u DOCKER_DEFAULT_PLATFORM harbor run -p sqlglot-window-visibility -a oracle -n 1 -k 1 -r 0 --jobs-dir runs/oracle
env -u DOCKER_DEFAULT_PLATFORM harbor run -p sqlglot-window-visibility -a nop    -n 1 -k 1 -r 0 --jobs-dir runs/nop
```

## Contents

```text
sqlglot-window-visibility/
├── instruction.md · task.toml
├── environment/                 Pinned task environment
├── solution/                    Reference fix
├── tests/                       Verifier and test fixtures
├── README.md
├── jobs/                        Preserved evaluation evidence
└── provenance.json              Original source catalog and packaging location
```

Upstream licence: [`../licenses/sqlglot-correlation-domains-LICENSE.txt`](../licenses/sqlglot-correlation-domains-LICENSE.txt)
(the SQLGlot MIT licence; the file name follows the source repository's notice index).
