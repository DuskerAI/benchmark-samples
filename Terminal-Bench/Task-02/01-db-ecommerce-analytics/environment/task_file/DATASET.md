# Dataset Background

The five CSV files contain synthetically generated e-commerce transaction data (random seed 42) covering the period 2022-01-01 to 2024-01-15.

- **customers.csv** — 1,000 customers with signup dates spread across the data period
- **orders.csv** — 17,538 orders; unit prices may differ from base prices due to simulated promotions
- **products.csv** — 50 products across 5 categories, one category per supplier
- **suppliers.csv** — 5 suppliers, one per product category
- **returns.csv** — 775 return records; contains intentional data quality issues (orphaned order IDs, over-returned quantities) that must be handled as described in the task instructions
