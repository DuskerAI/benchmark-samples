"""Tests for milestone 2. Run this file alone to verify only milestone 2: pytest tests/test_m2.py"""

import subprocess
import json

import requests

SERVER_URL = "http://127.0.0.1:3000"
DATA_FILE = "/app/data/dataset.csv"

REFERENCE_STATS = {
    "height_cm": {
        "mean": 170.036,
        "median": 169.785,
        "stddev": 9.8934,
        "min": 143.80,
        "max": 194.59,
        "p25": 164.1275,
        "p75": 176.2225,
    },
    "weight_kg": {
        "mean": 76.4224,
        "median": 76.495,
        "stddev": 11.3812,
        "min": 48.78,
        "max": 108.49,
        "p25": 68.985,
        "p75": 83.0825,
    },
    "age": {
        "mean": 41.03,
        "median": 41.0,
        "stddev": 12.628,
        "min": 18.0,
        "max": 64.0,
        "p25": 31.0,
        "p75": 52.0,
    },
    "income": {
        "mean": 54467.2285,
        "median": 54013.145,
        "stddev": 14527.2045,
        "min": 2874.92,
        "max": 93975.99,
        "p25": 45885.2625,
        "p75": 63296.01,
    },
    "test_score": {
        "mean": 73.9498,
        "median": 74.67,
        "stddev": 11.7932,
        "min": 43.16,
        "max": 101.22,
        "p25": 66.3575,
        "p75": 81.5425,
    },
}


def _fetch_compute_stats():
    """Retrieve computed statistics from the REST API."""
    resp = requests.get(f"{SERVER_URL}/api/compute?file={DATA_FILE}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def _run_engine_cli(mode="stats"):
    """Execute the native engine binary and parse its JSON output."""
    result = subprocess.run(
        ["/app/build/statengine", "--file", DATA_FILE, "--mode", mode],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Engine binary failed: {result.stderr}"
    return json.loads(result.stdout)


class TestMilestone2:
    """Tests for milestone 2: Numeric Accuracy."""

    def test_dataset_record_count(self):
        """Dataset has 200 rows; engine binary must report exactly 200."""
        data = _run_engine_cli()
        assert data["row_count"] == 200, f"Expected 200 rows, got {data['row_count']}"

    def test_engine_output_valid_json(self):
        """Engine binary output must be valid JSON."""
        _run_engine_cli()

    def test_engine_reports_all_columns(self):
        """Engine binary must report stats for all 5 numeric columns."""
        data = _run_engine_cli()
        for col in REFERENCE_STATS:
            assert col in data["columns"], f"Missing column: {col}"

    def test_engine_mean_accuracy(self):
        """Mean values from engine binary must match reference within tolerance."""
        data = _run_engine_cli()
        for col, ref in REFERENCE_STATS.items():
            actual = data["columns"][col]["mean"]
            assert isinstance(actual, float), (
                f"{col} mean must be float, got {type(actual).__name__}"
            )
            assert abs(actual - ref["mean"]) < 0.1, (
                f"{col} mean: expected {ref['mean']}, got {actual}"
            )

    def test_engine_median_accuracy(self):
        """Median values must use correct even-length averaging."""
        data = _run_engine_cli()
        for col, ref in REFERENCE_STATS.items():
            actual = data["columns"][col]["median"]
            assert isinstance(actual, float), (
                f"{col} median must be float, got {type(actual).__name__}"
            )
            assert abs(actual - ref["median"]) < 0.1, (
                f"{col} median: expected {ref['median']}, got {actual}"
            )

    def test_engine_stddev_accuracy(self):
        """Standard deviation must use sample correction (n-1)."""
        data = _run_engine_cli()
        for col, ref in REFERENCE_STATS.items():
            actual = data["columns"][col]["stddev"]
            assert isinstance(actual, float), (
                f"{col} stddev must be float, got {type(actual).__name__}"
            )
            assert abs(actual - ref["stddev"]) < 0.5, (
                f"{col} stddev: expected {ref['stddev']}, got {actual}"
            )

    def test_engine_min_max_are_floats(self):
        """Min and max values from engine binary must be floating-point numbers."""
        data = _run_engine_cli()
        for col, ref in REFERENCE_STATS.items():
            actual_min = data["columns"][col]["min"]
            actual_max = data["columns"][col]["max"]
            assert isinstance(actual_min, float), (
                f"{col} min must be float, got {type(actual_min).__name__}"
            )
            assert isinstance(actual_max, float), (
                f"{col} max must be float, got {type(actual_max).__name__}"
            )
            assert abs(actual_min - ref["min"]) < 0.1, (
                f"{col} min: expected {ref['min']}, got {actual_min}"
            )
            assert abs(actual_max - ref["max"]) < 0.1, (
                f"{col} max: expected {ref['max']}, got {actual_max}"
            )

    def test_engine_percentile_accuracy(self):
        """Percentile values (p25, p75) must use linear interpolation."""
        data = _run_engine_cli()
        for col, ref in REFERENCE_STATS.items():
            for pct in ["p25", "p75"]:
                actual = data["columns"][col][pct]
                assert isinstance(actual, float), f"{col} {pct} must be float"
                assert abs(actual - ref[pct]) < 0.5, (
                    f"{col} {pct}: expected {ref[pct]}, got {actual}"
                )

    def test_compute_endpoint_status(self):
        """GET /api/compute must return 200."""
        resp = requests.get(f"{SERVER_URL}/api/compute?file={DATA_FILE}", timeout=30)
        assert resp.status_code == 200

    def test_api_includes_stddev(self):
        """API response must include stddev (not null) for each column."""
        data = _fetch_compute_stats()
        for col in REFERENCE_STATS:
            stats = data["columns"][col]
            assert stats.get("stddev") is not None, (
                f"{col} stddev is null/missing in API response"
            )

    def test_api_engine_mean_parity(self):
        """API-served mean must match the engine binary output."""
        api_data = _fetch_compute_stats()
        c_data = _run_engine_cli()
        for col in REFERENCE_STATS:
            api_val = api_data["columns"][col]["mean"]
            c_val = c_data["columns"][col]["mean"]
            assert abs(api_val - c_val) < 0.001, (
                f"{col} mean mismatch: API={api_val}, engine={c_val}"
            )
