#!/usr/bin/env python3
"""Generate deterministic dataset for statistical dashboard testing."""
import random
import os

random.seed(42)

NUM_ROWS = 200
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "dataset.csv")

columns = ["id", "height_cm", "weight_kg", "age", "income", "test_score"]

rows = []
for i in range(1, NUM_ROWS + 1):
    height = round(random.gauss(170, 10), 2)
    weight = round(0.8 * height - 60 + random.gauss(0, 8), 2)
    age = random.randint(18, 65)
    income = round(random.gauss(55000, 15000), 2)
    score = round(random.gauss(75, 12), 2)
    rows.append([i, height, weight, age, income, score])

with open(OUTPUT_PATH, "w") as f:
    f.write(",".join(columns) + "\n")
    for row in rows:
        f.write(",".join(str(v) for v in row) + "\n")

print(f"Generated {OUTPUT_PATH} with {NUM_ROWS} rows")
