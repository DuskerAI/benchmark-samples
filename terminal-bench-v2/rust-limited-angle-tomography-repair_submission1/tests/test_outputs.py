from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path("/app")
DATA = APP / "data"
OUTPUT = APP / "output" / "reconstruction.json"
VISIBLE_EXPECTED = Path("/tests/expected/phantom.json")
HIDDEN_EXPECTED = Path("/tests/expected/hidden_case.json")
STRESS_EXPECTED = Path("/tests/expected/stress_case.json")
HIDDEN_DATA = Path("/tmp/tomo_hidden_case")
HIDDEN_OUTPUT = Path("/tmp/tomo_hidden_output.json")
CALIBRATED_DATA = Path("/tmp/tomo_calibrated_case")
CALIBRATED_OUTPUT = Path("/tmp/tomo_calibrated_output.json")
STRESS_DATA = Path("/tmp/tomo_stress_case")
STRESS_OUTPUT = Path("/tmp/tomo_stress_output.json")
CALIBRATED_STRESS_DATA = Path("/tmp/tomo_calibrated_stress_case")
CALIBRATED_STRESS_OUTPUT = Path("/tmp/tomo_calibrated_stress_output.json")

INPUT_HASHES = {
    "/app/data/geometry.json": "1af6060d32053fa810c3c08537176fbd8b7de92ad6c65884e7adb2711418524b",
    "/app/data/sinogram.json": "2976302563d6ed7620e2df093d223a32d5d8dafbb5890be696ae9e59c7311541",
    "/app/data/mask.json": "8320bf45553dd3b35a236fd3f7886a7bbe321f52ab8677cdb79b4d4680500560",
    "/app/data/calibration.json": "10e3ca145cb1b9ac72b19fe39a7daba49ee22e1cde7551819d2d85f158546170",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ray_weights(g, angle_deg, detector):
    theta = math.radians(angle_deg)
    nx, ny = math.cos(theta), math.sin(theta)
    tx, ty = -math.sin(theta), math.cos(theta)
    s = (detector - (g["detector_count"] - 1) / 2) * g["detector_spacing"]
    half = max(g["width"], g["height"]) * g["pixel_spacing"] * 0.76
    ds = (2 * half) / (g["sample_count"] - 1)
    acc = {}
    ox, oy = g["origin"]
    for k in range(g["sample_count"]):
        u = -half + k * ds
        x = s * nx + u * tx
        y = s * ny + u * ty
        ix = round((x - ox) / g["pixel_spacing"])
        iy = round((y - oy) / g["pixel_spacing"])
        if 0 <= ix < g["width"] and 0 <= iy < g["height"]:
            idx = iy * g["width"] + ix
            acc[idx] = acc.get(idx, 0.0) + ds
    return list(acc.items())


def _project(g, values):
    out = []
    for angle in g["angles_degrees"]:
        for d in range(g["detector_count"]):
            out.append(sum(values[idx] * w for idx, w in _ray_weights(g, angle, d)))
    return out


def _rel_l2(a, b):
    num = sum((x - y) ** 2 for x, y in zip(a, b))
    den = sum(y * y for y in b)
    return math.sqrt(num / den)


def _write_case(expected_path: Path, data_dir: Path):
    case = json.loads(expected_path.read_text())
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    (data_dir / "geometry.json").write_text(json.dumps(case["geometry"]) + "\n")
    (data_dir / "sinogram.json").write_text(json.dumps(case["sinogram"]) + "\n")
    (data_dir / "mask.json").write_text(json.dumps(case["mask"]) + "\n")
    (data_dir / "problem.md").write_text(case["problem_md"])
    (data_dir / "calibration.json").write_text(json.dumps(case["calibration"]) + "\n")
    return case


def _write_calibrated_case(expected_path: Path, data_dir: Path):
    case = json.loads(expected_path.read_text())
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    detector_count = case["geometry"]["detector_count"]
    gains = [1.08 + 0.006 * (d % 7) for d in range(detector_count)]
    darks = [0.31 + 0.015 * (d % 5) for d in range(detector_count)]
    raw = [
        m * gains[i % detector_count] + darks[i % detector_count]
        for i, m in enumerate(case["sinogram"]["measurements"])
    ]
    (data_dir / "geometry.json").write_text(json.dumps(case["geometry"]) + "\n")
    (data_dir / "sinogram.json").write_text(json.dumps({"measurements": raw}) + "\n")
    (data_dir / "mask.json").write_text(json.dumps(case["mask"]) + "\n")
    (data_dir / "problem.md").write_text(case["problem_md"])
    (data_dir / "calibration.json").write_text(
        json.dumps({"detector_gain": gains, "dark_current": darks, "angle_unit": "degrees"}) + "\n"
    )
    return case


def _write_mixed_calibrated_case(expected_path: Path, data_dir: Path):
    case = json.loads(expected_path.read_text())
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    detector_count = case["geometry"]["detector_count"]
    gain = 1.17
    darks = [0.23 + 0.011 * ((3 * d) % 11) for d in range(detector_count)]
    raw = [
        m * gain + darks[i % detector_count]
        for i, m in enumerate(case["sinogram"]["measurements"])
    ]
    (data_dir / "geometry.json").write_text(json.dumps(case["geometry"]) + "\n")
    (data_dir / "sinogram.json").write_text(json.dumps({"measurements": raw}) + "\n")
    (data_dir / "mask.json").write_text(json.dumps(case["mask"]) + "\n")
    (data_dir / "problem.md").write_text(case["problem_md"])
    (data_dir / "calibration.json").write_text(
        json.dumps({"detector_gain": gain, "dark_current": darks, "angle_unit": "degrees"}) + "\n"
    )
    return case


def _run_binary(data_dir: Path, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    run = subprocess.run(
        [str(APP / "target" / "release" / "tomo_repair"), str(data_dir), str(output)],
        cwd=APP,
        text=True,
        capture_output=True,
    )
    assert run.returncode == 0, run.stderr
    assert output.exists(), f"binary did not write {output}"
    return json.loads(output.read_text())


def _check_schema(solution, geometry):
    assert solution["width"] == geometry["width"]
    assert solution["height"] == geometry["height"]
    assert solution["pixel_spacing"] == pytest.approx(geometry["pixel_spacing"], abs=1e-12)
    assert solution["origin"] == pytest.approx(geometry["origin"], abs=1e-12)
    values = solution["values"]
    assert isinstance(values, list)
    assert len(values) == geometry["width"] * geometry["height"]
    assert all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
    assert min(values) >= -1e-9
    required_summary = {
        "min",
        "max",
        "mean",
        "active_mean",
        "active_energy",
        "measurement_residual",
    }
    assert set(solution["summary"]) >= required_summary
    for key in required_summary:
        value = solution["summary"][key]
        assert isinstance(value, (int, float)) and math.isfinite(value), f"summary.{key} is not finite"


def _check_quality(solution, geometry, measurements, truth, mask, limits):
    values = solution["values"]
    assert max(values) >= limits["max_min"], "reconstruction has no high-attenuation region"
    assert len({round(v, 3) for v in values}) >= limits["unique_min"], (
        "reconstruction is suspiciously quantized or constant"
    )
    pred = _project(geometry, values)
    residual = _rel_l2(pred, measurements)
    assert residual <= limits["residual"], f"relative scan residual {residual:.4f} is too high"
    assert solution["summary"]["measurement_residual"] == pytest.approx(residual, abs=1.5e-6)

    active = [i for i, m in enumerate(mask) if m]
    mae = sum(abs(values[i] - truth[i]) for i in active) / len(active)
    rmse = math.sqrt(sum((values[i] - truth[i]) ** 2 for i in active) / len(active))
    assert mae <= limits["mae"], f"masked MAE {mae:.4f} is too high"
    assert rmse <= limits["rmse"], f"masked RMSE {rmse:.4f} is too high"

    active_vals = [values[i] for i, m in enumerate(mask) if m]
    inactive_vals = [values[i] for i, m in enumerate(mask) if not m]
    leakage = sum(v * v for v in inactive_vals) / sum(v * v for v in active_vals)
    assert leakage <= limits["leakage"], f"outside-mask energy leakage {leakage:.4f} is too high"

    high_truth = [i for i, v in enumerate(truth) if v >= limits["high_truth"]]
    low_truth = [i for i, v in enumerate(truth) if mask[i] and v <= limits["low_truth"]]
    high_mean = sum(values[i] for i in high_truth) / len(high_truth)
    low_mean = sum(values[i] for i in low_truth) / len(low_truth)
    assert high_mean - low_mean >= limits["contrast"], "attenuation contrast is too weak"


@pytest.fixture(scope="session")
def built_binary():
    build = subprocess.run(
        ["cargo", "build", "--release", "--offline"],
        cwd=APP,
        text=True,
        capture_output=True,
    )
    assert build.returncode == 0, build.stderr
    return APP / "target" / "release" / "tomo_repair"


@pytest.fixture(scope="session")
def visible_case(built_binary):
    geometry = json.loads((DATA / "geometry.json").read_text())
    measurements = json.loads((DATA / "sinogram.json").read_text())["measurements"]
    expected = json.loads(VISIBLE_EXPECTED.read_text())
    solution = _run_binary(DATA, OUTPUT)
    return {
        "geometry": geometry,
        "measurements": measurements,
        "truth": expected["values"],
        "mask": expected["mask"],
        "solution": solution,
    }


@pytest.fixture(scope="session")
def hidden_case(built_binary):
    expected = _write_case(HIDDEN_EXPECTED, HIDDEN_DATA)
    solution = _run_binary(HIDDEN_DATA, HIDDEN_OUTPUT)
    return {
        "geometry": expected["geometry"],
        "measurements": expected["sinogram"]["measurements"],
        "truth": expected["truth"]["values"],
        "mask": expected["mask"]["active"],
        "solution": solution,
    }


@pytest.fixture(scope="session")
def calibrated_case(built_binary):
    expected = _write_calibrated_case(HIDDEN_EXPECTED, CALIBRATED_DATA)
    solution = _run_binary(CALIBRATED_DATA, CALIBRATED_OUTPUT)
    return {
        "geometry": expected["geometry"],
        "measurements": expected["sinogram"]["measurements"],
        "truth": expected["truth"]["values"],
        "mask": expected["mask"]["active"],
        "solution": solution,
    }


@pytest.fixture(scope="session")
def stress_case(built_binary):
    expected = _write_case(STRESS_EXPECTED, STRESS_DATA)
    solution = _run_binary(STRESS_DATA, STRESS_OUTPUT)
    return {
        "geometry": expected["geometry"],
        "measurements": expected["sinogram"]["measurements"],
        "truth": expected["truth"]["values"],
        "mask": expected["mask"]["active"],
        "solution": solution,
    }


@pytest.fixture(scope="session")
def calibrated_stress_case(built_binary):
    expected = _write_mixed_calibrated_case(STRESS_EXPECTED, CALIBRATED_STRESS_DATA)
    solution = _run_binary(CALIBRATED_STRESS_DATA, CALIBRATED_STRESS_OUTPUT)
    return {
        "geometry": expected["geometry"],
        "measurements": expected["sinogram"]["measurements"],
        "truth": expected["truth"]["values"],
        "mask": expected["mask"]["active"],
        "solution": solution,
    }


class TestInputIntegrity:
    """Verify the visible scan inputs were not modified by the agent."""

    def test_inputs_are_unchanged(self):
        """All protected /app/data files retain their expected SHA-256 digests."""
        for path, digest in INPUT_HASHES.items():
            p = Path(path)
            assert p.exists(), f"missing input {path}"
            assert _sha256(p) == digest, f"{path} was modified"


class TestCliContract:
    """Verify the binary rejects invalid CLI argument counts."""

    def test_rejects_wrong_argument_count(self, built_binary):
        """Zero, one, and three positional arguments fail with a usage message."""
        for args in ([], [str(DATA)], [str(DATA), str(OUTPUT), "extra"]):
            run = subprocess.run(
                [str(built_binary), *args],
                cwd=APP,
                text=True,
                capture_output=True,
            )
            assert run.returncode != 0, f"binary accepted invalid argument list {args}"
            assert "usage:" in run.stderr.lower()


class TestDeterministicOutput:
    """Verify repeated runs are stable and respect non-default output paths."""

    def test_repeated_runs_match_exactly(self, built_binary):
        """Two runs on the same scan and different output paths produce identical JSON bytes."""
        out1 = Path("/tmp/tomo_determinism/first/reconstruction.json")
        out2 = Path("/tmp/tomo_determinism/second/reconstruction.json")
        _run_binary(DATA, out1)
        _run_binary(DATA, out2)
        assert out1.read_text() == out2.read_text()


class TestVisibleCase:
    """Verify reconstruction on the visible /app/data scan."""

    def test_schema(self, visible_case):
        """Output JSON has the required shape, finite values, and summary fields."""
        _check_schema(visible_case["solution"], visible_case["geometry"])

    def test_reconstruction_quality(self, visible_case):
        """Visible /app/data: residual<=0.080, MAE<=0.095, leakage<=0.035, contrast>=0.70."""
        _check_quality(
            visible_case["solution"],
            visible_case["geometry"],
            visible_case["measurements"],
            visible_case["truth"],
            visible_case["mask"],
            {
                "max_min": 0.90,
                "unique_min": 120,
                "residual": 0.080,
                "mae": 0.095,
                "rmse": 0.155,
                "leakage": 0.035,
                "high_truth": 0.80,
                "low_truth": 0.05,
                "contrast": 0.70,
            },
        )


class TestHiddenPerturbedCase:
    """Verify reconstruction on a perturbed same-schema hidden scan."""

    def test_schema(self, hidden_case):
        """Hidden-case output JSON matches geometry and contains finite diagnostics."""
        _check_schema(hidden_case["solution"], hidden_case["geometry"])

    def test_reconstruction_quality(self, hidden_case):
        """Perturbed hidden scan: residual<=0.085, MAE<=0.075, leakage<=0.035, contrast>=0.62."""
        _check_quality(
            hidden_case["solution"],
            hidden_case["geometry"],
            hidden_case["measurements"],
            hidden_case["truth"],
            hidden_case["mask"],
            {
                "max_min": 0.70,
                "unique_min": 100,
                "residual": 0.085,
                "mae": 0.075,
                "rmse": 0.130,
                "leakage": 0.035,
                "high_truth": 0.72,
                "low_truth": 0.04,
                "contrast": 0.62,
            },
        )


class TestCalibratedPerturbedCase:
    """Verify numeric detector calibration is applied before reconstruction and residual reporting."""

    def test_schema(self, calibrated_case):
        """Calibrated-case output JSON matches geometry and contains finite diagnostics."""
        _check_schema(calibrated_case["solution"], calibrated_case["geometry"])

    def test_reconstruction_quality(self, calibrated_case):
        """Calibrated perturbed scan: per-detector normalization preserves the hidden target limits."""
        _check_quality(
            calibrated_case["solution"],
            calibrated_case["geometry"],
            calibrated_case["measurements"],
            calibrated_case["truth"],
            calibrated_case["mask"],
            {
                "max_min": 0.70,
                "unique_min": 100,
                "residual": 0.085,
                "mae": 0.075,
                "rmse": 0.130,
                "leakage": 0.035,
                "high_truth": 0.72,
                "low_truth": 0.04,
                "contrast": 0.62,
            },
        )


class TestStressIrregularCase:
    """Verify reconstruction on a narrower-angle irregular support stress scan."""

    def test_schema(self, stress_case):
        """Stress-case output JSON matches geometry and contains finite diagnostics."""
        _check_schema(stress_case["solution"], stress_case["geometry"])

    def test_reconstruction_quality(self, stress_case):
        """Stress scan: residual<=0.025, MAE<=0.068, leakage<=0.004, contrast>=0.78."""
        _check_quality(
            stress_case["solution"],
            stress_case["geometry"],
            stress_case["measurements"],
            stress_case["truth"],
            stress_case["mask"],
            {
                "max_min": 1.10,
                "unique_min": 180,
                "residual": 0.025,
                "mae": 0.068,
                "rmse": 0.110,
                "leakage": 0.004,
                "high_truth": 0.90,
                "low_truth": 0.04,
                "contrast": 0.78,
            },
        )


class TestCalibratedStressIrregularCase:
    """Verify calibration and irregular narrow-angle reconstruction interact correctly."""

    def test_schema(self, calibrated_stress_case):
        """Calibrated stress output JSON matches geometry and contains finite diagnostics."""
        _check_schema(calibrated_stress_case["solution"], calibrated_stress_case["geometry"])

    def test_reconstruction_quality(self, calibrated_stress_case):
        """Calibrated stress scan keeps residual<=0.025, leakage<=0.004, and contrast>=0.78."""
        _check_quality(
            calibrated_stress_case["solution"],
            calibrated_stress_case["geometry"],
            calibrated_stress_case["measurements"],
            calibrated_stress_case["truth"],
            calibrated_stress_case["mask"],
            {
                "max_min": 1.10,
                "unique_min": 180,
                "residual": 0.025,
                "mae": 0.068,
                "rmse": 0.110,
                "leakage": 0.004,
                "high_truth": 0.90,
                "low_truth": 0.04,
                "contrast": 0.78,
            },
        )
