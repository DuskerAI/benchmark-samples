Repair a Full-Stack Numeric Analysis Dashboard

A data visualization dashboard is at /app. It has three layers: a native computation engine written in C (CLI tool that reads CSV and outputs JSON), a Node.js/Express REST API layer that wraps the C binary, and a React frontend that displays charts and tables. The project uses CMake for the C build and Vite for the React frontend.

A previous developer built the full stack but introduced bugs across the C build system, numerical computations, the Express API layer, and the React frontend. The C code fails to compile, computed statistics are wrong, and the frontend doesn't display data correctly. Debug and fix all issues across all three layers.

To build the C binary: cd /app && mkdir -p build && cd build && cmake .. && make -j$(nproc)
To build the React frontend: cd /app/frontend && npm run build
To start the server: cd /app/server && node server.js

Data Source

/app/data/dataset.csv has 200 rows with columns: id, height_cm, weight_kg, age, income, test_score. The C binary reads this CSV and computes statistics per numeric column.

Native Computation Engine CLI

Usage: /app/build/statengine --file <path> [--mode stats|regression|full] [--help]

Modes: stats (descriptive statistics per column), regression (linear regression between first two numeric columns), full (both). Default mode is full. Output is JSON to stdout.

JSON Output Schema

The engine produces JSON with this structure. For full mode it includes all of the following; stats mode includes row_count and columns only; regression mode includes regression only.

{
  "row_count": 200,
  "columns": {
    "height_cm": {
      "mean": 170.5,
      "median": 170.0,
      "stddev": 10.2,
      "min": 150.0,
      "max": 195.0,
      "p25": 163.0,
      "p75": 178.0
    }
  },
  "regression": {
    "x_column": "height_cm",
    "y_column": "weight_kg",
    "slope": 0.85,
    "intercept": -70.2,
    "r_squared": 0.72
  }
}

REST API Layer (port 3000)

GET /health returns {"status": "ok"}.

GET /api/compute?file=/app/data/dataset.csv runs the C binary with --mode stats and returns parsed JSON with all column statistics.

GET /api/regression?file=/app/data/dataset.csv&x=height_cm&y=weight_kg runs the C binary with --mode regression, extracts the regression object, and returns a flat JSON response with top-level keys x_column, y_column, slope, intercept, r_squared (not nested under a regression wrapper).

GET / serves the built React frontend.

The C binary must compile via CMake and produce /app/build/statengine. Running statengine --help must print usage listing modes and options. The Express server must start on port 3000 and respond to GET /health. The React frontend must build successfully with npm run build, producing /app/frontend/dist/index.html.

GET /api/compute must return correct statistics for all numeric columns. Median for even-length arrays must average the two middle values (0-indexed: elements at n/2-1 and n/2 after sorting). Percentiles (p25, p75) must use linear interpolation between adjacent sorted values. Standard deviation must use sample correction (divide by n-1, not n). All numeric values must be floating-point (not truncated to integers). The CSV parser must process all 200 data rows (not skip any after the header).

The React frontend must fetch data from the correct API endpoints and display results. GET / must serve the built React app; the served HTML must contain a React root div (id="root") and display "Statistical Dashboard". The regression endpoint must return slope and intercept in the correct fields (not swapped). The C binary must produce valid JSON output for any well-formed CSV input.
