# Task 04 — Full-Stack Numeric Dashboard Repair (C · Node/Express · React)

**Suite:** Terminal-Bench · **Source task ID:** `06-numeric-dashboard-fullstack-repair` · **Category:** debugging · **Languages:** C, JavaScript, Python, Bash
**Difficulty:** hard · **Milestones:** 3 · **Expert / junior estimate:** 90 / 180 min · **Verifier:** 33 tests across 3 milestone suites

| Model (pass@4) | Passes | Solved |
| --- | :---: | :---: |
| Claude Opus 4.8 | **3 / 4** | Yes |
| GPT-5.5 | **3 / 3 scored** | Yes |

---

## Objective

A three-tier analytics dashboard has been left broken across every layer:

- a **native statistics engine in C** (CMake build; CLI that reads CSV and emits JSON);
- an **Express REST API** that shells out to the engine and reshapes its output;
- a **React + Vite frontend** that charts and tabulates the results.

The C build fails, the statistics are numerically wrong, the API mis-maps fields and the frontend
calls the wrong endpoints. The agent must restore an end-to-end working system that matches a precise
numerical contract: interpolated percentiles, sample standard deviation, even-length medians, float
typing, and all 200 data rows parsed. The prompt is
[`instruction.md`](06-numeric-dashboard-fullstack-repair/instruction.md). The milestones are listed in
[`milestones.md`](06-numeric-dashboard-fullstack-repair/milestones.md).

## Technical challenges

The reference solution documents 15 labelled repairs, grouped by milestone. Faults in one layer mask
faults in the next. For example, the stats are unobservable until the C build is fixed, and the API's
key mapping can't be checked until the engine emits valid JSON.

| Milestone | Layer | Representative faults (from the reference repair log) |
| --- | --- | --- |
| **M1: Build & boot** | CMake, C, Express | Phantom source in `CMakeLists.txt` and missing `libm` link; header/definition parameter-order mismatch in `stats.c`; wrong include in `csv_parser.c`; wrong default port in `server.js` |
| **M2: Numerical correctness** | C, API | Median off-by-one for even *n*; population vs. sample (Bessel) std-dev; JSON emitter with wrong format specifiers/escaping (ints vs floats); CSV parser silently drops the first data row; API key mismatch (`std` vs `stddev`) |
| **M3: Integration** | API, React, Vite | Wrong frontend endpoint path; swapped chart axis keys; wrong Vite proxy port; regression `slope`/`intercept` swapped in the API; React state overwritten by the regression response |

The task tests **cross-language debugging** (C memory and format semantics, Node child-process I/O, React
state) together with **numerical-methods precision**.

## Environment

| Item | Value |
| --- | --- |
| Base image | `node:20.18.1-bookworm` + gcc, make, cmake, Python 3 |
| Build steps | `npm ci` for server and frontend; dataset generated deterministically by `data/generate_data.py` (200 rows × 6 columns) |
| App layout | [`environment/app/`](06-numeric-dashboard-fullstack-repair/environment/app/): `src/` + `include/` (C), `server/` (Express), `frontend/` (React/Vite), `data/` |
| Resources | 2 CPUs · 4 GB RAM · 10 GB storage |
| Timeouts | agent 1200 s · verifier 450 s · build 600 s |

## Verification methodology

[`tests/test.sh`](06-numeric-dashboard-fullstack-repair/tests/test.sh) runs three milestone suites with
pytest (Python 3.13 via `uv`). [`conftest.py`](06-numeric-dashboard-fullstack-repair/tests/conftest.py)
builds the engine and frontend and manages the Express server lifecycle. Statistics are checked against
fixed reference values for the deterministically generated dataset, within tolerances of 0.1–0.5. API
values must also match the engine's own output (parity < 0.001), so the API layer can't paper over engine
errors. Reward is binary across all 33 tests.

| Suite | Tests | What it checks |
| --- | :---: | --- |
| [`test_m1.py`](06-numeric-dashboard-fullstack-repair/tests/test_m1.py) | 8 | CMake configure + build, binary present, `--help` usage, stats mode exits 0, `/health`, frontend build + `dist/` |
| [`test_m2.py`](06-numeric-dashboard-fullstack-repair/tests/test_m2.py) | 11 | 200 rows parsed; mean / median / std-dev / p25 / p75 accuracy; min/max are floats; API ↔ engine parity |
| [`test_m3.py`](06-numeric-dashboard-fullstack-repair/tests/test_m3.py) | 14 | Regression slope / intercept / R² and field names; root serves React (`id="root"`, title); frontend uses the correct API path; well-formed JSON on arbitrary CSV; full/default modes; sequential requests |

## Evaluation results

GPT-5.5 used high reasoning effort. Three Opus 4.8 attempts used the harness default. The fourth
(`DG6YYQB`) came from a high-effort top-up job (see [`../README.md`](../README.md#evaluation-protocol)).

| Model | Trial | Reward | Tests passed | Failing tests |
| --- | --- | :---: | :---: | --- |
| Claude Opus 4.8 | [`5CJHz8x`](evaluation/claude-opus-4.8/pass4-heavy-v2/06-numeric-dashboard-fullstack-r__5CJHz8x/verifier/test-stdout.txt) | 1 | 33 / 33 | — |
| Claude Opus 4.8 | [`ZUziGqW`](evaluation/claude-opus-4.8/pass4-heavy-v2/06-numeric-dashboard-fullstack-r__ZUziGqW/verifier/test-stdout.txt) | 1 | 33 / 33 | — |
| Claude Opus 4.8 | [`L4AdTDZ`](evaluation/claude-opus-4.8/pass4-heavy-v2/06-numeric-dashboard-fullstack-r__L4AdTDZ/verifier/test-stdout.txt) | 0 | 30 / 33 | M2: `median_accuracy`, `min_max_are_floats`, `percentile_accuracy` |
| Claude Opus 4.8 (high effort) | [`DG6YYQB`](evaluation/claude-opus-4.8/pass4-topup-040607-high-effort/06-numeric-dashboard-fullstack-r__DG6YYQB/verifier/test-stdout.txt) | 1 | 33 / 33 | — |
| GPT-5.5 | [`29S7spd`](evaluation/gpt-5.5/06-numeric-dashboard-fullstack-r__29S7spd/verifier/test-stdout.txt) | 1 | 33 / 33 | — |
| GPT-5.5 | [`JQqX4aG`](evaluation/gpt-5.5/06-numeric-dashboard-fullstack-r__JQqX4aG/verifier/test-stdout.txt) | 1 | 33 / 33 | — |
| GPT-5.5 | [`tUHW5re`](evaluation/gpt-5.5/06-numeric-dashboard-fullstack-r__tUHW5re/verifier/test-stdout.txt) | 1 | 33 / 33 | — |
| GPT-5.5 | [`7vv624c`](evaluation/gpt-5.5/06-numeric-dashboard-fullstack-r__7vv624c/verifier/test-stdout.txt) | not scored | (30 / 33) | API usage limit reached during the attempt |

**Pooled (scored attempts):** 6 / 7 passed. Trial directory names are truncated to 32 characters by the harness.

### Observed failure mode

The single valid failure (`L4AdTDZ`) cleared all build and integration milestones but missed the numeric
typing and interpolation contract. Integer-valued columns such as `age` were emitted as JSON integers
(`age median must be float, got int`), and the median and percentile checks failed alongside them. The
unscored GPT-5.5 attempt shows the same three M2 failures in its verifier log. This is the task's most subtle layer.

## Reproduce

```sh
harbor run -p 06-numeric-dashboard-fullstack-repair -a oracle   # reference fix → expected reward 1
harbor run -p 06-numeric-dashboard-fullstack-repair -a nop      # broken stack  → expected reward 0
```

[`solution/solve.sh`](06-numeric-dashboard-fullstack-repair/solution/solve.sh) applies the milestone
scripts cumulatively (`solve1.sh` → `solve3.sh`). `solve.sh --milestone N` stops after milestone *N*,
which allows per-milestone oracle checks.

## Contents

```text
Task-04/
├── 06-numeric-dashboard-fullstack-repair/   Runnable task package (unchanged from source)
│   ├── instruction.md · milestones.md · task.toml
│   ├── environment/                         Dockerfile + app/ (C engine, Express server, React frontend, data)
│   ├── solution/                            solve.sh → solve1.sh, solve2.sh, solve3.sh (per milestone)
│   └── tests/                               test.sh, conftest.py, test_m1.py, test_m2.py, test_m3.py
└── evaluation/
    ├── claude-opus-4.8/{pass4-heavy-v2, pass4-topup-040607-high-effort}/<trial>/
    └── gpt-5.5/<trial>/
```
