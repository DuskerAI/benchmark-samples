# Task 02 — E-commerce Analytics Pipeline (Go + DuckDB)

**Suite:** Terminal-Bench · **Source task ID:** `01-db-ecommerce-analytics` · **Category:** data-processing (db_interaction) · **Language:** Go
**Difficulty:** hard · **Expert / junior estimate:** 90 / 900 min · **Verifier:** 92 pytest assertions

| Model (pass@4) | Passes | Solved |
| --- | :---: | :---: |
| Claude Opus 4.8 | **2 / 4** | Yes |
| GPT-5.5 | **0 / 4** | No |

---

## Objective

The agent builds an offline analytics job in Go. It reads five relational CSV extracts and produces
nine JSON reports that a commerce analytics team would consume: revenue trends, cohort retention,
RFM segmentation, Pareto/ABC product classes and price-tier performance. It may use the standard
library, the pre-cached `go-duckdb` driver for SQL, or both, and the Go source must remain on disk.

| Input (`/app/input_data/`) | Rows | Notes |
| --- | ---: | --- |
| `orders.csv` | 17,538 | Line-level orders; promotional `unit_price` differs from base price |
| `customers.csv` | 1,000 | Signup dates spread over the period |
| `products.csv` | 50 | 5 categories, one supplier each |
| `suppliers.csv` | 5 | — |
| `returns.csv` | 775 | **Dirty:** orphaned order IDs and over-returned quantities |

Field definitions, sort orders and calculation rules live in
[`output_spec.json`](01-db-ecommerce-analytics/environment/task_file/output_spec.json), with input
schemas in [`schemas.json`](01-db-ecommerce-analytics/environment/task_file/schemas.json). The prompt is
[`instruction.md`](01-db-ecommerce-analytics/instruction.md).

## Technical challenges

1. **Window-function analytics.** 3-month rolling averages (`ROWS BETWEEN 2 PRECEDING AND CURRENT ROW`),
   month-over-month growth with null handling, cumulative revenue share for ABC classes, and three
   `NTILE` windows with deterministic tie-breakers for RFM scoring.
2. **Data-quality handling that propagates everywhere.** Orphaned returns must be dropped and
   `quantity_returned` capped at the ordered quantity before refunds are computed. Every net-revenue
   figure in all nine outputs depends on getting this right.
3. **Cohort mechanics.** Acquisition cohorts, month-2 retention with a minimum cohort size and exclusion
   of the trailing month, and a cohort × activity-month revenue matrix with `months_since_acquisition`.
4. **Derived segmentation.** Price tiers come from each product's *median transactional* price (not the
   catalogue base price), bucketed with `NTILE(3)`.
5. **Toolchain friction.** CGO-based DuckDB bindings in Go, decimal rounding to 2 places, and exact JSON
   field contracts across nine files.

| Output | Tests | Highlights |
| --- | :---: | --- |
| `top_customers.json` | 8 | Top 15 by net spend, AOV consistency |
| `monthly_revenue.json` | 13 | Gross/net, returns by processing month, rolling avg, MoM growth |
| `category_stats.json` | 7 | Category revenue, return-rate bounds |
| `supplier_share.json` | 7 | Shares sum to 100 |
| `customer_cohorts.json` | 8 | Month-2 retention, size ≥ 5, last month excluded |
| `product_performance.json` | 12 | ABC classes (A = top 70% of net revenue), cumulative share |
| `customer_segments.json` | 9 | RFM via `NTILE(4)` with tie-breakers |
| `price_cohort_performance.json` | 11 | Median-price tiers |
| `cohort_revenue_trend.json` | 14 | Cohort × activity-month matrix |
| Data quality / Go source | 3 | Orphan filtering, quantity capping, Go file present |

## Environment

| Item | Value |
| --- | --- |
| Base image | `python:3.11-slim` (digest-pinned), apt packages version-pinned |
| Toolchain | Go 1.22.5 + gcc (CGO); `github.com/marcboeker/go-duckdb v1.6.3` pre-fetched at build time |
| Resources | 1 CPU · 2 GB RAM · 5 GB storage |
| Timeouts | agent 1800 s · verifier 300 s · build 600 s |

See [`environment/Dockerfile`](01-db-ecommerce-analytics/environment/Dockerfile).

## Verification methodology

[`tests/test.sh`](01-db-ecommerce-analytics/tests/test.sh) first verifies the input CSVs against
`checksums.sha256` (tampering yields reward 0). It then runs
[`tests/test_outputs.py`](01-db-ecommerce-analytics/tests/test_outputs.py).

The verifier does **not** compare against stored answer files. It recomputes ground truth from the raw
CSVs with independent DuckDB 1.1.3 SQL (CTEs, window functions, `LEAST`-capped refunds), then compares
the agent's outputs row by row. It also checks internal invariants such as net = gross − returns,
shares summing to 100, and monotone cumulative percentages. Reward is binary.

## Evaluation results

GPT-5.5 used high reasoning effort. The Opus 4.8 attempts used the harness default
(see [`../README.md`](../README.md#evaluation-protocol)).

| Model | Trial | Reward | Tests passed | Failing tests |
| --- | --- | :---: | :---: | --- |
| Claude Opus 4.8 | [`5HDzftE`](evaluation/claude-opus-4.8/pass4-01-db-ecommerce-analytics/01-db-ecommerce-analytics__5HDzftE/verifier/test-stdout.txt) | 1 | 92 / 92 | — |
| Claude Opus 4.8 | [`ga7xfSF`](evaluation/claude-opus-4.8/pass4-01-db-ecommerce-analytics/01-db-ecommerce-analytics__ga7xfSF/verifier/test-stdout.txt) | 1 | 92 / 92 | — |
| Claude Opus 4.8 | [`DpmeAwr`](evaluation/claude-opus-4.8/pass4-01-db-ecommerce-analytics/01-db-ecommerce-analytics__DpmeAwr/verifier/test-stdout.txt) | 0 | 91 / 92 | `TestMonthlyRevenue::test_correct_row_count` |
| Claude Opus 4.8 | [`nXZm7nf`](evaluation/claude-opus-4.8/pass4-01-db-ecommerce-analytics/01-db-ecommerce-analytics__nXZm7nf/verifier/test-stdout.txt) | 0 | 91 / 92 | `TestMonthlyRevenue::test_correct_row_count` |
| GPT-5.5 | [`ACHaRLC`](evaluation/gpt-5.5/01-db-ecommerce-analytics__ACHaRLC/verifier/test-stdout.txt) | 0 | 91 / 92 | `TestMonthlyRevenue::test_correct_row_count` |
| GPT-5.5 | [`XQ79nEu`](evaluation/gpt-5.5/01-db-ecommerce-analytics__XQ79nEu/verifier/test-stdout.txt) | 0 | 91 / 92 | `TestMonthlyRevenue::test_correct_row_count` |
| GPT-5.5 | [`BfcWfLu`](evaluation/gpt-5.5/01-db-ecommerce-analytics__BfcWfLu/verifier/test-stdout.txt) | 0 | 89 / 92 | monthly row count; `TestCustomerCohorts::test_cohort_sizes_match`, `test_retained_counts_match` |
| GPT-5.5 | [`Y5Y9M8r`](evaluation/gpt-5.5/01-db-ecommerce-analytics__Y5Y9M8r/verifier/test-stdout.txt) | 0 | 89 / 92 | monthly row count; `TestCustomerCohorts::test_cohort_sizes_match`, `test_retained_counts_match` |

**Pooled:** 2 / 8 trials passed (25%). This is the hardest task in the Terminal-Bench sample by pooled pass rate.

### Observed failure modes

- **Month set of `monthly_revenue.json` (6 of 6 failures).** Orders run from 2022-01 to 2023-12, but some
  returns are processed in **2024-01**, a month with no orders. The reference query aggregates by
  order month and left-joins returns, so it yields 24 months. Failing solutions also emitted a
  returns-only 25th month (`Expected 24 months, got 25`).
- **Cohort basis (2 GPT-5.5 failures).** The reference defines a cohort by each customer's **first-order
  month**. Two GPT-5.5 solutions produced different cohort sizes (e.g., `cohort_size mismatch for 2022-01: 39 vs 14`),
  which suggests a different cohort basis such as signup month.

In every failure, all other outputs (RFM, ABC, price tiers, cohort-revenue matrix, data-quality
handling) matched the independent ground truth.

## Reproduce

```sh
harbor run -p 01-db-ecommerce-analytics -a oracle   # reference solution → expected reward 1
harbor run -p 01-db-ecommerce-analytics -a nop      # no changes        → expected reward 0
```

The reference solution ([`solution/solve.sh`](01-db-ecommerce-analytics/solution/solve.sh)) generates and
runs a Go program that uses `go-duckdb` for the SQL stages.

## Contents

```text
Task-02/
├── 01-db-ecommerce-analytics/        Runnable task package (unchanged from source)
│   ├── instruction.md · task.toml
│   ├── environment/                  Dockerfile + task_file/ (CSV inputs, schemas, output_spec, examples)
│   ├── solution/solve.sh             Reference (oracle) solution
│   └── tests/                        test.sh + test_outputs.py (verifier)
└── evaluation/
    ├── claude-opus-4.8/pass4-01-db-ecommerce-analytics/<trial>/
    └── gpt-5.5/<trial>/
```
