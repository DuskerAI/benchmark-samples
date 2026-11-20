# E-Commerce Analytics Task

This directory contains all resources for the DuckDB e-commerce analytics task.

## Directory Layout

```
task_file/
├── input_data/          CSV data files (orders, customers, products, suppliers, returns)
├── output_data/         Write your 9 JSON output files here
├── examples/            Representative rows for each of the 9 output files
├── schemas.json         Column names and types for each input CSV
├── output_spec.json     Exact calculation rules, sort orders, and rounding for outputs
└── DATASET.md           Background on the synthetic dataset
```

## Workflow

1. Read schemas.json to understand the input CSV columns.
2. Read output_spec.json to understand what each output file must contain.
3. Use DuckDB to query the CSVs in input_data/ and compute the results.
4. Write the nine JSON files to output_data/.

See examples/ for representative rows from each output file.
