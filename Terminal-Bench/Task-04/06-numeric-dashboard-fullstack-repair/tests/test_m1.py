"""Tests for milestone 1. Run this file alone to verify only milestone 1: pytest tests/test_m1.py"""

import subprocess
from pathlib import Path

import requests

SERVER_URL = "http://127.0.0.1:3000"


class TestMilestone1:
    """Tests for milestone 1: Compilation & Connectivity."""

    def test_build_config_passes(self):
        """CMake configuration must complete without errors."""
        code = Path("/tmp/cmake_exit_code").read_text().strip()
        assert code == "0", f"cmake exited with code {code}"

    def test_native_build_passes(self):
        """C compilation must succeed and produce the binary."""
        code = Path("/tmp/build_exit_code").read_text().strip()
        assert code == "0", f"make exited with code {code}"

    def test_engine_binary_present(self):
        """The statengine binary must exist at /app/build/statengine."""
        assert Path("/app/build/statengine").exists(), "statengine binary not found"

    def test_usage_flag_output(self):
        """Running --help must print usage info listing all modes."""
        result = subprocess.run(
            ["/app/build/statengine", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"--help exited with {result.returncode}"
        out = result.stdout.lower()
        assert "stats" in out, "Help must mention stats mode"
        assert "regression" in out, "Help must mention regression mode"
        assert "full" in out, "Help must mention full mode"
        assert "--file" in out, "Help must mention --file option"

    def test_stats_mode_exits_cleanly(self):
        """Running --mode stats on the dataset must exit 0."""
        result = subprocess.run(
            [
                "/app/build/statengine",
                "--file",
                "/app/data/dataset.csv",
                "--mode",
                "stats",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"stats mode exited {result.returncode}: {result.stderr}"
        )

    def test_server_health_check(self):
        """REST API server must respond to GET /health with status ok."""
        resp = requests.get(f"{SERVER_URL}/health", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"

    def test_frontend_build_passes(self):
        """React frontend must build without errors."""
        code = Path("/tmp/frontend_build_exit_code").read_text().strip()
        assert code == "0", f"frontend build exited with code {code}"

    def test_frontend_dist_present(self):
        """Vite build must produce dist/index.html."""
        assert Path("/app/frontend/dist/index.html").exists(), (
            "frontend dist/index.html not found"
        )
