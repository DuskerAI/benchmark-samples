"""Tests for milestone 3. Run this file alone to verify only milestone 3: pytest tests/test_m3.py"""

import subprocess
import json
from pathlib import Path

import requests

SERVER_URL = "http://127.0.0.1:3000"
DATA_FILE = "/app/data/dataset.csv"

REFERENCE_REGRESSION = {
    "slope": 0.770136,
    "intercept": -54.528384,
    "r_squared": 0.448181,
}


class TestMilestone3:
    """Tests for milestone 3: Rendering & End-to-End Integration."""

    def test_regression_endpoint_status(self):
        """GET /api/regression must return 200."""
        resp = requests.get(
            f"{SERVER_URL}/api/regression?file={DATA_FILE}&x=height_cm&y=weight_kg",
            timeout=30,
        )
        assert resp.status_code == 200

    def test_linear_fit_slope(self):
        """Regression slope must match reference value (not swapped with intercept)."""
        resp = requests.get(
            f"{SERVER_URL}/api/regression?file={DATA_FILE}&x=height_cm&y=weight_kg",
            timeout=30,
        )
        data = resp.json()
        actual = data["slope"]
        assert isinstance(actual, float), (
            f"slope must be float, got {type(actual).__name__}"
        )
        assert abs(actual) < 10, (
            f"slope should be < 10 (got {actual}), likely swapped with intercept"
        )
        assert abs(actual - REFERENCE_REGRESSION["slope"]) < 0.05, (
            f"slope: expected {REFERENCE_REGRESSION['slope']}, got {actual}"
        )

    def test_linear_fit_intercept(self):
        """Regression intercept must match reference value."""
        resp = requests.get(
            f"{SERVER_URL}/api/regression?file={DATA_FILE}&x=height_cm&y=weight_kg",
            timeout=30,
        )
        data = resp.json()
        actual = data["intercept"]
        assert isinstance(actual, float), (
            f"intercept must be float, got {type(actual).__name__}"
        )
        assert abs(actual - REFERENCE_REGRESSION["intercept"]) < 2.0, (
            f"intercept: expected {REFERENCE_REGRESSION['intercept']}, got {actual}"
        )

    def test_coefficient_of_determination(self):
        """R-squared must be between 0 and 1 and match reference."""
        resp = requests.get(
            f"{SERVER_URL}/api/regression?file={DATA_FILE}&x=height_cm&y=weight_kg",
            timeout=30,
        )
        data = resp.json()
        actual = data["r_squared"]
        assert isinstance(actual, float), "r_squared must be float"
        assert 0 <= actual <= 1, f"r_squared must be in [0,1], got {actual}"
        assert abs(actual - REFERENCE_REGRESSION["r_squared"]) < 0.05, (
            f"r_squared: expected {REFERENCE_REGRESSION['r_squared']}, got {actual}"
        )

    def test_regression_x_column_field(self):
        """Regression JSON must include x_column matching the requested column."""
        resp = requests.get(
            f"{SERVER_URL}/api/regression?file={DATA_FILE}&x=height_cm&y=weight_kg",
            timeout=30,
        )
        data = resp.json()
        assert "x_column" in data, "regression response must include x_column"
        assert data["x_column"] == "height_cm", (
            f"x_column: expected 'height_cm', got '{data['x_column']}'"
        )

    def test_regression_y_column_field(self):
        """Regression JSON must include y_column matching the requested column."""
        resp = requests.get(
            f"{SERVER_URL}/api/regression?file={DATA_FILE}&x=height_cm&y=weight_kg",
            timeout=30,
        )
        data = resp.json()
        assert "y_column" in data, "regression response must include y_column"
        assert data["y_column"] == "weight_kg", (
            f"y_column: expected 'weight_kg', got '{data['y_column']}'"
        )

    def test_engine_regression_json_valid(self):
        """Engine binary --mode regression must produce valid JSON."""
        result = subprocess.run(
            ["/app/build/statengine", "--file", DATA_FILE, "--mode", "regression"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "regression" in data

    def test_root_serves_html(self):
        """GET / must return HTML with React root div."""
        resp = requests.get(f"{SERVER_URL}/", timeout=10)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert 'id="root"' in resp.text, "HTML must contain React root div"

    def test_root_contains_dashboard_title(self):
        """Served HTML must include the dashboard title."""
        resp = requests.get(f"{SERVER_URL}/", timeout=10)
        assert "Statistical Dashboard" in resp.text

    def test_frontend_uses_correct_compute_path(self):
        """frontend api.js must call /api/compute, not /api/stats."""
        content = Path("/app/frontend/src/api.js").read_text()
        assert "/api/compute" in content, (
            "frontend api.js must use /api/compute endpoint"
        )
        assert "/api/stats" not in content, (
            "frontend api.js still calls non-existent /api/stats"
        )

    def test_engine_produces_wellformed_json(self):
        """Engine binary stats output must be well-formed JSON with expected structure."""
        result = subprocess.run(
            ["/app/build/statengine", "--file", DATA_FILE, "--mode", "stats"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)
        assert "columns" in data

    def test_full_mode_combined_json(self):
        """Engine binary --mode full must produce JSON with both columns and regression."""
        result = subprocess.run(
            ["/app/build/statengine", "--file", DATA_FILE, "--mode", "full"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "columns" in data, "full mode must include columns"
        assert "regression" in data, "full mode must include regression"
        assert "row_count" in data, "full mode must include row_count"

    def test_default_mode_is_full(self):
        """Running without --mode must default to full (columns + regression)."""
        result = subprocess.run(
            ["/app/build/statengine", "--file", DATA_FILE],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"default mode exited {result.returncode}: {result.stderr}"
        )
        data = json.loads(result.stdout)
        assert "columns" in data, "default mode must include columns"
        assert "regression" in data, "default mode must include regression"
        assert "row_count" in data, "default mode must include row_count"

    def test_sequential_api_requests(self):
        """Fetching stats then regression must both return valid data."""
        stats_resp = requests.get(
            f"{SERVER_URL}/api/compute?file={DATA_FILE}", timeout=30
        )
        assert stats_resp.status_code == 200
        stats_data = stats_resp.json()
        assert "columns" in stats_data

        reg_resp = requests.get(
            f"{SERVER_URL}/api/regression?file={DATA_FILE}&x=height_cm&y=weight_kg",
            timeout=30,
        )
        assert reg_resp.status_code == 200
        reg_data = reg_resp.json()
        assert "slope" in reg_data
        assert "intercept" in reg_data
