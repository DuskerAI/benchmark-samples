"""Behavioral verifier for the Haskell lens calibrator task."""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

APP = Path("/app/lenscal")
BIN = APP / "lenscal"


def run_cmd(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command with deterministic locale and captured output."""
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, env=env, timeout=30, check=False)
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def build() -> None:
    """Build the Haskell project before invoking the task binary."""
    run_cmd(["make", "-C", str(APP), "clean", "all"])
    assert BIN.exists(), "build did not create /app/lenscal/lenscal"


def test_pytest_command_ignores_app_shadow_module(tmp_path: Path) -> None:
    """The verifier command must not import a pytest.py file created under /app."""
    shadow = Path("/app/pytest.py")
    shadow.write_text("raise SystemExit(99)\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONSAFEPATH"] = "1"
    result = subprocess.run(
        ["python", "-c", "import pathlib, pytest; print(pathlib.Path(pytest.__file__).resolve())"],
        cwd="/app",
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
        check=False,
    )
    shadow.unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() != "/app/pytest.py"


def write_case(root: Path, name: str, rows: list[dict[str, float | str]], camera: dict[str, float]) -> Path:
    """Write one calibration case using the public case-file schema."""
    case = root / name
    case.mkdir(parents=True, exist_ok=True)
    (case / "camera.txt").write_text("".join(f"{k}={camera[k]}\n" for k in ["width", "height", "cx", "cy", "focal"]), encoding="utf-8")
    with (case / "observations.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["frame", "marker", "x", "y", "u", "v", "weight"])
        writer.writeheader()
        writer.writerows(rows)
    return case


def synth_rows(camera: dict[str, float], frames: dict[str, tuple[float, float]], k1: float, k2: float, *, outlier: bool = False) -> list[dict[str, float | str]]:
    """Generate deterministic same-schema observations from the public radial model."""
    board = [(-0.24, -0.18), (0.0, -0.18), (0.24, -0.18), (-0.18, 0.02), (0.18, 0.02), (-0.24, 0.23), (0.0, 0.23), (0.24, 0.23)]
    rows: list[dict[str, float | str]] = []
    for frame, (tx, ty) in frames.items():
        for idx, (x, y) in enumerate(board):
            qx, qy = x + tx, y + ty
            r2 = qx * qx + qy * qy
            fac = 1 + k1 * r2 + k2 * r2 * r2
            nx, ny = fac * qx, fac * qy
            rows.append({
                "frame": frame,
                "marker": f"M{idx:02d}",
                "x": x,
                "y": y,
                "u": camera["cx"] + camera["focal"] * nx,
                "v": camera["cy"] + camera["focal"] * ny,
                "weight": 1.0 + (idx % 3) * 0.25,
            })
    if outlier:
        rows[len(rows) // 2] = dict(rows[len(rows) // 2])
        rows[len(rows) // 2]["u"] = float(rows[len(rows) // 2]["u"]) + 48.0
        rows[len(rows) // 2]["v"] = float(rows[len(rows) // 2]["v"]) - 31.0
    order = [5, 2, 13, 0, 21, 8, 17, 1, 18, 11, 6, 15, 3, 20, 10, 4, 23, 9, 12, 7, 22, 14, 19, 16]
    usable = [i for i in order if i < len(rows)]
    return [rows[i] for i in usable]


def gaussian(a: list[list[float]], b: list[float]) -> list[float]:
    """Solve a dense linear system with partial pivoting."""
    n = len(b)
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            raise AssertionError("reference system is singular")
        a[col], a[piv] = a[piv], a[col]
        b[col], b[piv] = b[piv], b[col]
        div = a[col][col]
        for j in range(col, n):
            a[col][j] /= div
        b[col] /= div
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            for j in range(col, n):
                a[r][j] -= factor * a[col][j]
            b[r] -= factor * b[col]
    return b


def solve_reference(camera: dict[str, float], rows: list[dict[str, float | str]]) -> dict[str, object]:
    """Compute an independent reference fit from the public two-pass Gauss-Newton contract."""
    obs = [{"frame": str(r["frame"]), "x": float(r["x"]), "y": float(r["y"]), "nx": (float(r["u"]) - camera["cx"]) / camera["focal"], "ny": (float(r["v"]) - camera["cy"]) / camera["focal"], "weight": float(r["weight"])} for r in rows]
    frames = sorted({o["frame"] for o in obs})
    index = {f: i for i, f in enumerate(frames)}
    p = [0.0, 0.0]
    for f in frames:
        group = [o for o in obs if o["frame"] == f]
        sw = sum(o["weight"] for o in group)
        p.extend([sum(o["weight"] * (o["nx"] - o["x"]) for o in group) / sw, sum(o["weight"] * (o["ny"] - o["y"]) for o in group) / sw])

    def residuals(params: list[float]) -> list[float]:
        vals = []
        for o in obs:
            off = 2 + 2 * index[o["frame"]]
            qx, qy = o["x"] + params[off], o["y"] + params[off + 1]
            r2 = qx * qx + qy * qy
            fac = 1 + params[0] * r2 + params[1] * r2 * r2
            vals.append(math.hypot(o["nx"] - fac * qx, o["ny"] - fac * qy))
        return vals

    def gauss_newton(params: list[float], robust: list[float]) -> tuple[list[float], int]:
        n = len(params)
        for it in range(1, 31):
            a = [[0.0] * n for _ in range(n)]
            b = [0.0] * n
            for o, rw in zip(obs, robust):
                off = 2 + 2 * index[o["frame"]]
                qx, qy = o["x"] + params[off], o["y"] + params[off + 1]
                r2 = qx * qx + qy * qy
                r4 = r2 * r2
                k1, k2 = params[0], params[1]
                fac = 1 + k1 * r2 + k2 * r4
                predx, predy = fac * qx, fac * qy
                gx = [0.0] * n
                gy = [0.0] * n
                gx[0], gx[1] = r2 * qx, r4 * qx
                gy[0], gy[1] = r2 * qy, r4 * qy
                gx[off] = fac + 2 * k1 * qx * qx + 4 * k2 * r2 * qx * qx
                gx[off + 1] = 2 * k1 * qx * qy + 4 * k2 * r2 * qx * qy
                gy[off] = gx[off + 1]
                gy[off + 1] = fac + 2 * k1 * qy * qy + 4 * k2 * r2 * qy * qy
                w = o["weight"] * rw
                for g, res in ((gx, o["nx"] - predx), (gy, o["ny"] - predy)):
                    for i in range(n):
                        b[i] += w * g[i] * res
                        for j in range(n):
                            a[i][j] += w * g[i] * g[j]
            for i in range(n):
                a[i][i] += 1e-8
            delta = gaussian(a, b)
            params = [x + d for x, d in zip(params, delta)]
            if max(abs(d) for d in delta) < 1e-11:
                return params, it
        return params, 30

    p, first_iterations = gauss_newton(p, [1.0] * len(obs))
    rs = sorted(residuals(p))
    med = rs[len(rs) // 2] if len(rs) % 2 else 0.5 * (rs[len(rs) // 2 - 1] + rs[len(rs) // 2])
    scale = max(1e-9, 1.4826 * med)
    robust = [min(1.0, (3 * scale) / max(r, 1e-12)) for r in residuals(p)]
    p, second_iterations = gauss_newton(p, robust)
    final = residuals(p)
    cutoff = max(0.004, 4 * scale)
    outliers = [r > cutoff for r in final]
    frame_entries = []
    for f in frames:
        ids = [i for i, o in enumerate(obs) if o["frame"] == f]
        in_rs = [final[i] for i in ids if not outliers[i]]
        off = 2 + 2 * index[f]
        frame_entries.append({"frame": f, "tx": p[off], "ty": p[off + 1], "rms": math.sqrt(sum(r * r for r in in_rs) / len(in_rs)), "inliers": len(in_rs), "outliers": len(ids) - len(in_rs)})
    in_final = [r for i, r in enumerate(final) if not outliers[i]]
    return {"k1": p[0], "k2": p[1], "frames": frame_entries, "weighted_rms": math.sqrt(sum(r * r for r in in_final) / len(in_final)), "inliers": len(in_final), "outliers": sum(outliers), "iterations": first_iterations + second_iterations}


def assert_close(actual: float, expected: float, tol: float, label: str) -> None:
    """Assert finite approximate equality with a useful label."""
    assert math.isfinite(actual), f"{label} is not finite"
    assert abs(actual - expected) <= tol, f"{label}: expected {expected}, got {actual}"


def assert_report_repeats_camera(report: dict[str, object], camera: dict[str, float]) -> None:
    """The JSON report must repeat the public five-field camera record."""
    got = report["camera"]
    assert isinstance(got, dict)
    assert set(got) == {"width", "height", "cx", "cy", "focal"}
    for key, expected in camera.items():
        assert_close(float(got[key]), expected, 5e-7, f"camera {key}")


def assert_numeric_json_is_finite_and_decimal(text: str) -> None:
    """Every numeric report field must be finite and rendered with at least six decimals."""
    numeric_keys = {"width", "height", "cx", "cy", "focal", "k1", "k2", "tx", "ty", "rms", "inliers", "outliers", "observations", "weighted_rms", "iterations"}
    seen: set[str] = set()
    for key, value in re.findall(r'"([A-Za-z0-9_]+)":(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)', text):
        assert key in numeric_keys, f"unexpected numeric key {key}"
        seen.add(key)
        assert math.isfinite(float(value)), f"{key} is not finite"
        mantissa = value.split("e", 1)[0].split("E", 1)[0]
        assert "." in mantissa and len(mantissa.rsplit(".", 1)[1]) >= 6, f"{key} is not rendered to six decimals: {value}"
    assert numeric_keys <= seen


def assert_invalid(case: Path, out: Path) -> None:
    """Invalid cases must exit nonzero and remove a stale requested report."""
    out.write_text("stale", encoding="utf-8")
    result = run_cmd([str(BIN), str(case), str(out)], check=False)
    assert result.returncode != 0
    assert not out.exists(), f"invalid case {case.name} left a report"


def test_multiframe_distortion_and_robust_weighting(tmp_path: Path) -> None:
    """The report must match an independent robust bundle fit with a shuffled outlier case."""
    build()
    camera = {"width": 1920.0, "height": 1280.0, "cx": 967.5, "cy": 631.25, "focal": 875.0}
    rows = synth_rows(camera, {"B": (0.07, -0.025), "A": (-0.045, 0.055), "C": (0.115, 0.03)}, -0.19, 0.115, outlier=True)
    case = write_case(tmp_path, "robust_case", rows, camera)
    out = tmp_path / "report.json"
    out.write_text("old report must be replaced", encoding="utf-8")
    run_cmd([str(BIN), str(case), str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    ref = solve_reference(camera, rows)
    assert report["status"] == "ok"
    assert report["case"] == "robust_case"
    assert_report_repeats_camera(report, camera)
    assert_close(report["distortion"]["k1"], ref["k1"], 5e-5, "k1")
    assert_close(report["distortion"]["k2"], ref["k2"], 8e-4, "k2")
    assert report["summary"]["outliers"] == ref["outliers"] == 1
    assert report["summary"]["inliers"] == ref["inliers"]
    assert [f["frame"] for f in report["frames"]] == ["A", "B", "C"]
    for got, exp in zip(report["frames"], ref["frames"]):
        assert_close(got["tx"], exp["tx"], 8e-5, f"{got['frame']} tx")
        assert_close(got["ty"], exp["ty"], 8e-5, f"{got['frame']} ty")
        assert_close(got["rms"], exp["rms"], 8e-6, f"{got['frame']} rms")
        assert got["inliers"] == exp["inliers"]
        assert got["outliers"] == exp["outliers"]
    assert_close(report["summary"]["weighted_rms"], ref["weighted_rms"], 8e-6, "summary weighted_rms")
    assert report["summary"]["iterations"] == ref["iterations"]
    assert 2 <= report["summary"]["iterations"] <= 60


def test_input_order_independence_and_byte_determinism(tmp_path: Path) -> None:
    """The same logical case in a different row order must produce byte-identical JSON."""
    build()
    camera = {"width": 1400.0, "height": 1100.0, "cx": 700.0, "cy": 560.0, "focal": 910.0}
    rows = synth_rows(camera, {"R2": (0.032, -0.071), "R1": (-0.083, 0.019), "R3": (0.101, 0.082)}, -0.135, 0.052)
    parent1 = tmp_path / "run_a"
    parent2 = tmp_path / "run_b"
    case1 = write_case(parent1, "row_order_case", rows, camera)
    case2 = write_case(parent2, "row_order_case", list(reversed(rows)), camera)
    out1 = tmp_path / "one.json"
    out2 = tmp_path / "two.json"
    run_cmd([str(BIN), str(case1), str(out1)])
    run_cmd([str(BIN), str(case2), str(out2)])
    raw1 = out1.read_text(encoding="utf-8")
    raw2 = out2.read_text(encoding="utf-8")
    assert raw1 == raw2
    assert_numeric_json_is_finite_and_decimal(raw1)
    j1 = json.loads(raw1)
    j2 = json.loads(raw2)
    assert j1 == j2
    assert_report_repeats_camera(j1, camera)
    assert [f["frame"] for f in j1["frames"]] == sorted(f["frame"] for f in j1["frames"])
    ref = solve_reference(camera, rows)
    for got, exp in zip(j1["frames"], ref["frames"]):
        assert_close(got["rms"], exp["rms"], 8e-6, f"{got['frame']} rms")
    assert_close(j1["summary"]["weighted_rms"], ref["weighted_rms"], 8e-6, "summary weighted_rms")
    assert j1["summary"]["iterations"] == ref["iterations"]
    assert 2 <= j1["summary"]["iterations"] <= 60


def test_degenerate_case_fails_without_report(tmp_path: Path) -> None:
    """A singular one-frame observation set must be rejected and must not leave stale output."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"ONLY": (0.0, 0.0)}, -0.1, 0.03)[:6]
    case = write_case(tmp_path, "degenerate", rows, camera)
    out = tmp_path / "bad.json"
    out.write_text("stale", encoding="utf-8")
    result = run_cmd([str(BIN), str(case), str(out)], check=False)
    assert result.returncode != 0
    assert not out.exists(), "invalid runs must remove any stale report"


def test_extra_camera_keys_fail_without_report(tmp_path: Path) -> None:
    """Camera files with keys outside the public five-field set must be rejected."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"A": (-0.02, 0.04), "B": (0.05, -0.03)}, -0.11, 0.04)[:10]
    case = write_case(tmp_path, "extra_camera_key", rows, camera)
    with (case / "camera.txt").open("a", encoding="utf-8") as f:
        f.write("serial=detector-7\n")
    assert_invalid(case, tmp_path / "extra_camera_key.json")


def test_empty_frame_and_marker_identifiers_fail_without_report(tmp_path: Path) -> None:
    """Observation rows with an empty frame or marker identifier are invalid."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"A": (-0.02, 0.04), "B": (0.05, -0.03)}, -0.11, 0.04)[:10]
    for field in ("frame", "marker"):
        bad_rows = [dict(r) for r in rows]
        bad_rows[2][field] = ""
        case = write_case(tmp_path, f"empty_{field}", bad_rows, camera)
        assert_invalid(case, tmp_path / f"empty_{field}.json")


def test_identifier_characters_outside_public_set_fail_without_report(tmp_path: Path) -> None:
    """Frame and marker identifiers may only use the public ASCII identifier character set."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"A": (-0.02, 0.04), "B": (0.05, -0.03)}, -0.11, 0.04)[:10]
    for field, value in (("frame", "frame A"), ("marker", "M/02"), ("frame", "café"), ("marker", "M✓02")):
        bad_rows = [dict(r) for r in rows]
        bad_rows[2][field] = value
        case = write_case(tmp_path, f"bad_{field}_characters", bad_rows, camera)
        assert_invalid(case, tmp_path / f"bad_{field}_characters.json")


def test_wrong_observation_header_fails_without_report(tmp_path: Path) -> None:
    """The observation CSV must use the exact public header in the exact order."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"A": (-0.02, 0.04), "B": (0.05, -0.03)}, -0.11, 0.04)[:10]
    case = write_case(tmp_path, "wrong_observation_header", rows, camera)
    with (case / "observations.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["marker", "frame", "x", "y", "u", "v", "weight"])
        writer.writeheader()
        writer.writerows(rows)
    assert_invalid(case, tmp_path / "wrong_observation_header.json")


def test_nonpositive_observation_weights_fail_without_report(tmp_path: Path) -> None:
    """Observation weights must be positive finite values."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"A": (-0.02, 0.04), "B": (0.05, -0.03)}, -0.11, 0.04)[:10]
    for label, value in (("zero", "0"), ("negative", "-0.5")):
        bad_rows = [dict(r) for r in rows]
        bad_rows[4]["weight"] = value
        case = write_case(tmp_path, f"{label}_weight", bad_rows, camera)
        assert_invalid(case, tmp_path / f"{label}_weight.json")


def test_malformed_and_nonfinite_observation_coordinates_fail_without_report(tmp_path: Path) -> None:
    """Observation x, y, u, and v values must be finite decimal numbers."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"A": (-0.02, 0.04), "B": (0.05, -0.03)}, -0.11, 0.04)[:10]
    bad_values = (
        ("x", "not-a-number"),
        ("x", "nan"),
        ("y", "1.0.2"),
        ("y", "-inf"),
        ("u", "NaN"),
        ("u", "12px"),
        ("v", "inf"),
        ("v", ""),
    )
    for field, value in bad_values:
        bad_rows = [dict(r) for r in rows]
        bad_rows[3][field] = value
        label = re.sub(r"[^A-Za-z0-9_]+", "_", value.replace("-", "neg").replace(".", "dot")) or "empty"
        case = write_case(tmp_path, f"bad_observation_{field}_{label}", bad_rows, camera)
        assert_invalid(case, tmp_path / f"bad_observation_{field}_{label}.json")


def test_nonpositive_and_nonfinite_camera_values_fail_without_report(tmp_path: Path) -> None:
    """Camera values must be finite, and width, height, and focal must be positive."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"A": (-0.02, 0.04), "B": (0.05, -0.03)}, -0.11, 0.04)[:10]
    bad_values = [("width", "0"), ("height", "-1200"), ("focal", "0"), ("cx", "nan"), ("cy", "inf"), ("focal", "nan")]
    for key, value in bad_values:
        case = write_case(tmp_path, f"bad_camera_{key}_{value.replace('-', 'neg')}", rows, camera)
        lines = []
        for camera_key in ["width", "height", "cx", "cy", "focal"]:
            rendered = value if camera_key == key else str(camera[camera_key])
            lines.append(f"{camera_key}={rendered}\n")
        (case / "camera.txt").write_text("".join(lines), encoding="utf-8")
        assert_invalid(case, tmp_path / f"bad_camera_{key}_{value.replace('-', 'neg')}.json")


def test_missing_camera_file_fails_without_report(tmp_path: Path) -> None:
    """A case directory with observations but no camera file must not produce a report."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"A": (-0.01, 0.02), "B": (0.04, -0.05)}, -0.08, 0.025)[:10]
    case = tmp_path / "missing_camera"
    case.mkdir()
    with (case / "observations.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["frame", "marker", "x", "y", "u", "v", "weight"])
        writer.writeheader()
        writer.writerows(rows)
    assert_invalid(case, tmp_path / "missing_camera.json")


def test_empty_observation_set_fails_without_report(tmp_path: Path) -> None:
    """An observations file containing only the public header is invalid."""
    build()
    case = tmp_path / "empty_observations"
    case.mkdir()
    (case / "camera.txt").write_text("width=1600\nheight=1200\ncx=800\ncy=600\nfocal=900\n", encoding="utf-8")
    (case / "observations.csv").write_text("frame,marker,x,y,u,v,weight\n", encoding="utf-8")
    assert_invalid(case, tmp_path / "empty_observations.json")


def test_multiframe_fewer_than_five_observations_fail_without_report(tmp_path: Path) -> None:
    """Every frame in a multi-frame case must contribute at least five observations."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"A": (0.01, -0.02), "B": (-0.04, 0.03)}, -0.1, 0.04)[:8]
    assert {str(r["frame"]) for r in rows} == {"A", "B"}
    case = write_case(tmp_path, "short_frame", rows, camera)
    assert_invalid(case, tmp_path / "short_frame.json")


def test_singular_rank_fails_without_report(tmp_path: Path) -> None:
    """A multi-frame case with enough rows but a singular calibration system is invalid."""
    build()
    case = tmp_path / "singular_rank"
    case.mkdir()
    (case / "camera.txt").write_text("width=1600\nheight=1200\ncx=800\ncy=600\nfocal=900\n", encoding="utf-8")
    rows: list[dict[str, float | str]] = []
    for fr, nx, ny in (("A", 0.1, 0.2), ("B", -0.05, 0.15)):
        rows.extend(
            {"frame": fr, "marker": f"M{idx}", "x": 0.0, "y": 0.0, "u": 800.0 + 900.0 * nx, "v": 600.0 + 900.0 * ny, "weight": 1.0}
            for idx in range(5)
        )
    with (case / "observations.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["frame", "marker", "x", "y", "u", "v", "weight"])
        writer.writeheader()
        writer.writerows(rows)
    assert_invalid(case, tmp_path / "singular_rank.json")


def test_malformed_schema_variants_fail_cleanly(tmp_path: Path) -> None:
    """Malformed camera and observation files must fail nonzero without stale reports."""
    build()
    camera = {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0}
    rows = synth_rows(camera, {"A": (-0.02, 0.04), "B": (0.05, -0.03)}, -0.11, 0.04)[:10]
    bad_camera = tmp_path / "bad_camera"
    bad_camera.mkdir()
    (bad_camera / "camera.txt").write_text("width=1600\nheight=1200\ncx=800\ncy=600\nfocal=900\nfocal=901\n", encoding="utf-8")
    with (bad_camera / "observations.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["frame", "marker", "x", "y", "u", "v", "weight"])
        writer.writeheader()
        writer.writerows(rows)
    out = tmp_path / "stale.json"
    out.write_text("stale", encoding="utf-8")
    result = run_cmd([str(BIN), str(bad_camera), str(out)], check=False)
    assert result.returncode != 0
    assert not out.exists()

    bad_obs = tmp_path / "bad_obs"
    bad_obs.mkdir()
    (bad_obs / "camera.txt").write_text("width=1600\nheight=1200\ncx=800\ncy=600\nfocal=900\n", encoding="utf-8")
    bad_rows = [dict(r) for r in rows]
    bad_rows[3]["weight"] = "nan"
    with (bad_obs / "observations.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["frame", "marker", "x", "y", "u", "v", "weight"])
        writer.writeheader()
        writer.writerows(bad_rows)
    out.write_text("stale", encoding="utf-8")
    result = run_cmd([str(BIN), str(bad_obs), str(out)], check=False)
    assert result.returncode != 0
    assert not out.exists()

    missing_obs = tmp_path / "missing_obs"
    missing_obs.mkdir()
    (missing_obs / "camera.txt").write_text("width=1600\nheight=1200\ncx=800\ncy=600\nfocal=900\n", encoding="utf-8")
    out.write_text("stale", encoding="utf-8")
    result = run_cmd([str(BIN), str(missing_obs), str(out)], check=False)
    assert result.returncode != 0
    assert not out.exists()


def test_visible_fixture_uses_public_normalization(tmp_path: Path) -> None:
    """The bundled nominal fixture must be calibrated with cx, cy, and focal normalization."""
    build()
    source = APP / "cases" / "nominal"
    case = tmp_path / "nominal"
    shutil.copytree(source, case)
    out = tmp_path / "nominal.json"
    run_cmd([str(BIN), str(case), str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert_report_repeats_camera(report, {"width": 1600.0, "height": 1200.0, "cx": 800.0, "cy": 600.0, "focal": 900.0})
    assert report["summary"]["observations"] == 16
    assert report["summary"]["outliers"] == 0
    assert_close(report["distortion"]["k1"], -0.16, 2e-3, "visible k1")
    assert_close(report["distortion"]["k2"], 0.07, 8e-3, "visible k2")
