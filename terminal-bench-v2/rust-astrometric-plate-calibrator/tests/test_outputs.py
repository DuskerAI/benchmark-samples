"""Verifier for the Rust astrometric plate calibration task."""
from __future__ import annotations

import csv
import hashlib
import math
import shutil
import subprocess
import tempfile
from itertools import combinations
from pathlib import Path

APP = Path("/app")
OBS = APP / "observations"
DOCS = APP / "docs"
CONFIG = APP / "config"
BIN = APP / "target" / "release" / "astrofit"
PLATE_OUT = APP / "output" / "plate_solutions.csv"
CAL_OUT = APP / "output" / "calibrated_sources.csv"

PLATE_HEADER = [
    "exposure_id",
    "matched_sources",
    "rms_arcsec",
    "center_ra_deg",
    "center_dec_deg",
    "scale_arcsec_px",
    "rotation_deg",
]

CAL_HEADER = [
    "exposure_id",
    "detection_id",
    "source_id",
    "ra_deg",
    "dec_deg",
    "residual_arcsec",
]

INPUT_HASHES = {
    APP / "README.md": "2cce1841059f23a02347891b25211e3f06f2d6853b0060e0bb489cdd0443ab9f",
    CONFIG / "instrument.csv": "4df8c63c0d6652b415220cf756b42463b239b6ffbf9e0b87d92bd8df17a0c1ba",
    CONFIG / "quality_limits.csv": "09c70193d4847213412b98be791e496e2193c7c5c06d2ab732873f97bb76cf2c",
    OBS / "catalog.csv": "2ee8bbe533478c3a29b05af22a846b2a1f556613cd0d24647e9288d4362c7038",
    OBS / "exposures.csv": "8fb734d4ff7b81fb0c9bf0aef34c590fba778701ecf30e1673628b458a71306e",
    OBS / "exposure_wrap_2024a.csv": "36e02af8ab5c874b1961c7419a3c3fd2332473e790290ee2025efda0bd3a11c6",
    OBS / "exposure_crowded_2025b.csv": "c1bcc243a95ffd4c41e0fbb084b5a3c30ff98ad7986a0bcad64f40e4640614f9",
    OBS / "exposure_offaxis_2023c.csv": "c67fef45cf4d4737c82f4e230d4f8d7d451c703379fd97310b39fbda1db885b5",
    OBS / "manifest.csv": "aa4e6add8466c92d474914f522aa2cb250391ba49084793bb2eccd82a7b43a18",
}

DEG = math.pi / 180.0
RAD = 180.0 / math.pi
ARCSEC_PER_RAD = 206_264.806_247_096_36
MAX_FIT_RMS_ARCSEC = 0.05
MAX_FIT_RESIDUAL_ARCSEC = 0.08


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def normalize_ra(ra: float) -> float:
    return ra % 360.0


def wrap_delta_rad(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def propagate(src: dict[str, str], epoch: float) -> dict[str, float | str]:
    dec0 = float(src["dec_deg"])
    dt = epoch - float(src["epoch_year"])
    ra = float(src["ra_deg"]) + (
        float(src["pm_ra_mas_yr"]) * dt / 3_600_000.0
    ) / math.cos(dec0 * DEG)
    dec = dec0 + float(src["pm_dec_mas_yr"]) * dt / 3_600_000.0
    return {"source_id": src["source_id"], "ra_deg": normalize_ra(ra), "dec_deg": dec}


def sky_to_tangent(center_ra: float, center_dec: float, ra: float, dec: float) -> tuple[float, float]:
    a0 = center_ra * DEG
    d0 = center_dec * DEG
    a = ra * DEG
    d = dec * DEG
    da = wrap_delta_rad(a - a0)
    cosc = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(da)
    xi = math.cos(d) * math.sin(da) / cosc
    eta = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(da)) / cosc
    return xi * ARCSEC_PER_RAD, eta * ARCSEC_PER_RAD


def tangent_to_sky(center_ra: float, center_dec: float, xi_arcsec: float, eta_arcsec: float) -> tuple[float, float]:
    a0 = center_ra * DEG
    d0 = center_dec * DEG
    xi = xi_arcsec / ARCSEC_PER_RAD
    eta = eta_arcsec / ARCSEC_PER_RAD
    denom = math.cos(d0) - eta * math.sin(d0)
    ra = a0 + math.atan2(xi, denom)
    dec = math.atan2(
        math.sin(d0) + eta * math.cos(d0),
        math.sqrt(xi * xi + denom * denom),
    )
    return normalize_ra(ra * RAD), dec * RAD


def angular_sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    da = wrap_delta_rad((ra2 - ra1) * DEG)
    d1 = dec1 * DEG
    d2 = dec2 * DEG
    cos_sep = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(da)
    return math.acos(max(-1.0, min(1.0, cos_sep))) * ARCSEC_PER_RAD


def detection_to_sky(exp: dict[str, str], det: dict[str, str]) -> tuple[float, float]:
    dx, dy = undistorted_offset(exp, det)
    theta = float(exp["rotation_deg"]) * DEG
    xi = float(exp["scale_arcsec_px"]) * (math.cos(theta) * dx - math.sin(theta) * dy)
    eta = float(exp["scale_arcsec_px"]) * (math.sin(theta) * dx + math.cos(theta) * dy)
    return tangent_to_sky(float(exp["center_ra_deg"]), float(exp["center_dec_deg"]), xi, eta)


def undistorted_offset(exp: dict[str, str], det: dict[str, str]) -> tuple[float, float]:
    dx_obs = float(det["x_px"]) - float(exp["crpix_x"])
    dy_obs = float(det["y_px"]) - float(exp["crpix_y"])
    k = float(exp["radial_k"])
    dx = dx_obs
    dy = dy_obs
    for _ in range(8):
        r2 = dx * dx + dy * dy
        factor = 1.0 + k * r2
        dx = dx_obs / factor
        dy = dy_obs / factor
    return dx, dy


def solve_3x3(a: list[list[float]], b: list[float]) -> list[float]:
    m = [row[:] + [rhs] for row, rhs in zip(a, b)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        assert abs(m[pivot][col]) > 1.0e-12, "singular plate fit"
        m[col], m[pivot] = m[pivot], m[col]
        scale = m[col][col]
        for j in range(col, 4):
            m[col][j] /= scale
        for r in range(3):
            if r == col:
                continue
            factor = m[r][col]
            for j in range(col, 4):
                m[r][j] -= factor * m[col][j]
    return [m[i][3] for i in range(3)]


def fit_affine(offsets: list[tuple[float, float]], targets: list[tuple[float, float]]) -> tuple[float, float, float, float, float, float]:
    normal = [[0.0] * 3 for _ in range(3)]
    rhs_x = [0.0, 0.0, 0.0]
    rhs_y = [0.0, 0.0, 0.0]
    for (dx, dy), (xi, eta) in zip(offsets, targets):
        row = [dx, dy, 1.0]
        for i in range(3):
            rhs_x[i] += row[i] * xi
            rhs_y[i] += row[i] * eta
            for j in range(3):
                normal[i][j] += row[i] * row[j]
    a, b, c = solve_3x3(normal, rhs_x)
    d, e, f = solve_3x3(normal, rhs_y)
    return a, b, c, d, e, f


def assign_sources(
    skies: list[tuple[str, float, float]],
    propagated: list[dict[str, float | str]],
) -> dict[str, dict[str, float | str]]:
    candidate_lists = []
    for detection_id, ra, dec in skies:
        ranked = sorted(
            (
                (
                    angular_sep_arcsec(ra, dec, float(src["ra_deg"]), float(src["dec_deg"])),
                    idx,
                )
                for idx, src in enumerate(propagated)
            ),
            key=lambda item: (item[0], str(propagated[item[1]]["source_id"])),
        )
        candidate_lists.append((detection_id, ra, dec, ranked[: min(8, len(ranked))]))

    order = sorted(range(len(candidate_lists)), key=lambda i: candidate_lists[i][3][0][0])
    best_cost = float("inf")
    best_assignment: dict[str, int] | None = None

    def dfs(pos: int, used: set[int], total: float, picked: dict[str, int]) -> None:
        nonlocal best_cost, best_assignment
        if total >= best_cost:
            return
        if pos == len(order):
            best_cost = total
            best_assignment = dict(picked)
            return
        det_index = order[pos]
        detection_id, _ra, _dec, candidates = candidate_lists[det_index]
        for dist, src_idx in candidates:
            if src_idx in used:
                continue
            used.add(src_idx)
            picked[detection_id] = src_idx
            dfs(pos + 1, used, total + dist, picked)
            used.remove(src_idx)
            picked.pop(detection_id)

    dfs(0, set(), 0.0, {})
    assert best_assignment is not None, "no one-to-one source assignment found"
    return {det_id: propagated[src_idx] for det_id, src_idx in best_assignment.items()}


def expected_products(obs_dir: Path) -> tuple[dict[str, dict[str, float | int | str]], dict[tuple[str, str], dict[str, float | str]]]:
    catalog = read_csv(obs_dir / "catalog.csv")
    exposures = read_csv(obs_dir / "exposures.csv")
    plate_rows: dict[str, dict[str, float | int | str]] = {}
    cal_rows: dict[tuple[str, str], dict[str, float | str]] = {}
    for exp in exposures:
        propagated = [propagate(src, float(exp["epoch_year"])) for src in catalog]
        skies = []
        offsets = []
        detections = read_csv(obs_dir / exp["file"])
        for det in detections:
            ra, dec = detection_to_sky(exp, det)
            skies.append((det["detection_id"], ra, dec))
            offsets.append(undistorted_offset(exp, det))
        solution = best_exposure_solution(exp, skies, offsets, propagated)
        assert solution is not None, f"no valid robust plate solution for {exp['exposure_id']}"
        accepted_indices, assigned, aff, _candidate_residuals = solution
        residuals: list[float] = []
        for idx in accepted_indices:
            det_id, _ra, _dec = skies[idx]
            dx, dy = offsets[idx]
            a, b, c, d, e, f = aff
            xi = a * dx + b * dy + c
            eta = d * dx + e * dy + f
            ra, dec = tangent_to_sky(float(exp["center_ra_deg"]), float(exp["center_dec_deg"]), xi, eta)
            best = assigned[det_id]
            resid = angular_sep_arcsec(ra, dec, float(best["ra_deg"]), float(best["dec_deg"]))
            residuals.append(resid)
            cal_rows[(exp["exposure_id"], det_id)] = {
                "exposure_id": exp["exposure_id"],
                "detection_id": det_id,
                "source_id": best["source_id"],
                "ra_deg": ra,
                "dec_deg": dec,
                "residual_arcsec": resid,
            }
        a, b, c, d, e, f = aff
        fitted_center_ra, fitted_center_dec = tangent_to_sky(
            float(exp["center_ra_deg"]),
            float(exp["center_dec_deg"]),
            c,
            f,
        )
        determinant = a * e - b * d
        fitted_scale = math.sqrt(abs(determinant))
        fitted_rotation = math.atan2(d, a) * RAD
        plate_rows[exp["exposure_id"]] = {
            "exposure_id": exp["exposure_id"],
            "matched_sources": len(residuals),
            "rms_arcsec": math.sqrt(sum(r * r for r in residuals) / len(residuals)),
            "center_ra_deg": fitted_center_ra,
            "center_dec_deg": fitted_center_dec,
            "scale_arcsec_px": fitted_scale,
            "rotation_deg": fitted_rotation,
        }
    return plate_rows, cal_rows


def best_exposure_solution(
    exp: dict[str, str],
    skies: list[tuple[str, float, float]],
    offsets: list[tuple[float, float]],
    propagated: list[dict[str, float | str]],
):
    best = None
    n = len(skies)
    max_size = min(n, len(propagated))
    for size in range(max_size, 2, -1):
        for indices_tuple in combinations(range(n), size):
            indices = list(indices_tuple)
            subset_skies = [skies[i] for i in indices]
            subset_offsets = [offsets[i] for i in indices]
            try:
                assigned = assign_sources(subset_skies, propagated)
                targets = []
                for det_id, _ra, _dec in subset_skies:
                    src = assigned[det_id]
                    targets.append(
                        sky_to_tangent(
                            float(exp["center_ra_deg"]),
                            float(exp["center_dec_deg"]),
                            float(src["ra_deg"]),
                            float(src["dec_deg"]),
                        )
                    )
                aff = fit_affine(subset_offsets, targets)
            except AssertionError:
                continue
            residuals = []
            a, b, c, d, e, f = aff
            for (det_id, _ra, _dec), (dx, dy) in zip(subset_skies, subset_offsets):
                xi = a * dx + b * dy + c
                eta = d * dx + e * dy + f
                ra, dec = tangent_to_sky(float(exp["center_ra_deg"]), float(exp["center_dec_deg"]), xi, eta)
                src = assigned[det_id]
                residuals.append(angular_sep_arcsec(ra, dec, float(src["ra_deg"]), float(src["dec_deg"])))
            rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
            if rms > MAX_FIT_RMS_ARCSEC or max(residuals) > MAX_FIT_RESIDUAL_ARCSEC:
                continue
            tie = (rms, tuple(skies[i][0] for i in indices))
            if best is None or tie < best[0]:
                best = (tie, indices, assigned, aff, residuals)
        if best is not None:
            return best[1:]
    return None


def parse_plate(path: Path) -> dict[str, dict[str, float | int | str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == PLATE_HEADER, f"bad plate header: {reader.fieldnames}"
        rows = {}
        for row in reader:
            rows[row["exposure_id"]] = {
                "exposure_id": row["exposure_id"],
                "matched_sources": int(row["matched_sources"]),
                "rms_arcsec": float(row["rms_arcsec"]),
                "center_ra_deg": float(row["center_ra_deg"]),
                "center_dec_deg": float(row["center_dec_deg"]),
                "scale_arcsec_px": float(row["scale_arcsec_px"]),
                "rotation_deg": float(row["rotation_deg"]),
            }
    return rows


def parse_calibrated(path: Path) -> dict[tuple[str, str], dict[str, float | str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == CAL_HEADER, f"bad calibrated header: {reader.fieldnames}"
        rows = {}
        for row in reader:
            rows[(row["exposure_id"], row["detection_id"])] = {
                "exposure_id": row["exposure_id"],
                "detection_id": row["detection_id"],
                "source_id": row["source_id"],
                "ra_deg": float(row["ra_deg"]),
                "dec_deg": float(row["dec_deg"]),
                "residual_arcsec": float(row["residual_arcsec"]),
            }
    return rows


def run_binary(obs_dir: Path, plate_out: Path, cal_out: Path) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    plate_out.parent.mkdir(parents=True, exist_ok=True)
    cal_out.parent.mkdir(parents=True, exist_ok=True)
    run = subprocess.run(
        [str(BIN), str(obs_dir), str(plate_out), str(cal_out)],
        cwd=APP,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, f"astrofit failed\nstdout={run.stdout}\nstderr={run.stderr}"
    assert plate_out.exists(), f"missing {plate_out}"
    assert cal_out.exists(), f"missing {cal_out}"
    return parse_plate(plate_out), parse_calibrated(cal_out)


def compare_products(obs_dir: Path, plate_out: Path, cal_out: Path) -> None:
    expected_plate, expected_cal = expected_products(obs_dir)
    actual_plate, actual_cal = run_binary(obs_dir, plate_out, cal_out)
    assert set(actual_plate) == set(expected_plate)
    assert set(actual_cal) == set(expected_cal)
    for exposure_id, expected in expected_plate.items():
        actual = actual_plate[exposure_id]
        assert actual["matched_sources"] == expected["matched_sources"]
        assert abs(actual["rms_arcsec"] - expected["rms_arcsec"]) < 0.015
        assert actual["rms_arcsec"] < 0.035
        center_error = angular_sep_arcsec(
            actual["center_ra_deg"],
            actual["center_dec_deg"],
            expected["center_ra_deg"],
            expected["center_dec_deg"],
        )
        assert center_error < 0.02, f"{exposure_id} fitted center differs by {center_error:.4f} arcsec"
        assert abs(actual["scale_arcsec_px"] - expected["scale_arcsec_px"]) < 2.0e-5, f"{exposure_id} scale"
        assert abs(actual["rotation_deg"] - expected["rotation_deg"]) < 0.002, f"{exposure_id} rotation"
    for key, expected in expected_cal.items():
        actual = actual_cal[key]
        assert actual["source_id"] == expected["source_id"], f"{key} matched wrong source"
        sep = angular_sep_arcsec(
            actual["ra_deg"],
            actual["dec_deg"],
            expected["ra_deg"],
            expected["dec_deg"],
        )
        assert sep < 0.025, f"{key} sky coordinate differs by {sep:.4f} arcsec"
        assert abs(actual["residual_arcsec"] - expected["residual_arcsec"]) < 0.02
        assert 0.0 <= actual["ra_deg"] < 360.0
    by_exposure: dict[str, list[str]] = {}
    for actual in actual_cal.values():
        by_exposure.setdefault(str(actual["exposure_id"]), []).append(str(actual["source_id"]))
    for exposure_id, source_ids in by_exposure.items():
        assert len(source_ids) == len(set(source_ids)), f"{exposure_id} reused a source assignment"


def write_holdout_observations(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    exposures = [
        {
            "exposure_id": "holdout_wrap",
            "file": "holdout_wrap.csv",
            "center_ra_deg": 0.01842,
            "center_dec_deg": 1.14971,
            "_true_center_ra_deg": 0.018,
            "_true_center_dec_deg": 1.15,
            "epoch_year": 2026.0,
            "crpix_x": 2040.0,
            "crpix_y": 2030.0,
            "scale_arcsec_px": 0.6807,
            "rotation_deg": -32.94,
            "_true_scale_arcsec_px": 0.68,
            "_true_rotation_deg": -33.0,
            "radial_k": 2.8e-8,
        },
        {
            "exposure_id": "holdout_south",
            "file": "holdout_south.csv",
            "center_ra_deg": 78.21958,
            "center_dec_deg": -42.34966,
            "_true_center_ra_deg": 78.22,
            "_true_center_dec_deg": -42.35,
            "epoch_year": 2022.4,
            "crpix_x": 1888.0,
            "crpix_y": 2112.0,
            "scale_arcsec_px": 1.0288,
            "rotation_deg": 58.07,
            "_true_scale_arcsec_px": 1.03,
            "_true_rotation_deg": 58.0,
            "radial_k": -2.5e-8,
        },
    ]
    offsets = {
        "holdout_wrap": [(-310, 90), (-145, -155), (22.000, 20.000), (22.030, 20.000), (135, 170), (285, -95), (420, 240)],
        "holdout_south": [(-460, -260), (-220, 140), (-75, -35), (68, -38), (185, 220), (335, -170), (510, 85)],
    }
    catalog = []
    for exp in exposures:
        for idx, (xi, eta) in enumerate(offsets[exp["exposure_id"]], 1):
            sid = f"H{exp['exposure_id'][-1].upper()}{idx:03d}"
            ra_obs, dec_obs = tangent_to_sky(exp["_true_center_ra_deg"], exp["_true_center_dec_deg"], xi, eta)
            pm_ra = (-22.0 + idx * 9.25) if idx % 2 else (35.0 - idx * 4.5)
            pm_dec = (18.0 - idx * 5.75) if idx % 3 else (-31.0 + idx)
            epoch = 2015.5
            dt = exp["epoch_year"] - epoch
            ra_cat = normalize_ra(ra_obs - (pm_ra * dt / 3_600_000.0) / math.cos(dec_obs * DEG))
            dec_cat = dec_obs - pm_dec * dt / 3_600_000.0
            catalog.append(
                {
                    "source_id": sid,
                    "ra_deg": f"{ra_cat:.10f}",
                    "dec_deg": f"{dec_cat:.10f}",
                    "pm_ra_mas_yr": f"{pm_ra:.6f}",
                    "pm_dec_mas_yr": f"{pm_dec:.6f}",
                    "epoch_year": f"{epoch:.2f}",
                    "mag": f"{12.0 + idx * 0.41:.3f}",
                    "_exp": exp,
                    "_xi": xi,
                    "_eta": eta,
                    "_ra_obs": ra_obs,
                    "_dec_obs": dec_obs,
                }
            )
    with (root / "catalog.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["source_id", "ra_deg", "dec_deg", "pm_ra_mas_yr", "pm_dec_mas_yr", "epoch_year", "mag"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in catalog:
            writer.writerow({name: row[name] for name in fields})
    with (root / "exposures.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "exposure_id",
            "file",
            "center_ra_deg",
            "center_dec_deg",
            "epoch_year",
            "crpix_x",
            "crpix_y",
            "scale_arcsec_px",
            "rotation_deg",
            "radial_k",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for exp in exposures:
            writer.writerow({name: f"{exp[name]:.12g}" if isinstance(exp[name], float) else exp[name] for name in fields})
    for exp in exposures:
        rows = [row for row in catalog if row["_exp"] is exp]
        rows = rows[2:] + rows[:2]
        with (root / exp["file"]).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["detection_id", "x_px", "y_px", "flux"])
            writer.writeheader()
            for idx, row in enumerate(rows, 1):
                if exp["exposure_id"] == "holdout_wrap" and abs(row["_xi"] - 22.000) < 1e-9:
                    det_ra, det_dec = tangent_to_sky(exp["_true_center_ra_deg"], exp["_true_center_dec_deg"], 22.005, 20.000)
                elif exp["exposure_id"] == "holdout_wrap" and abs(row["_xi"] - 22.030) < 1e-9:
                    det_ra, det_dec = tangent_to_sky(exp["_true_center_ra_deg"], exp["_true_center_dec_deg"], 22.010, 20.000)
                else:
                    det_ra, det_dec = row["_ra_obs"], row["_dec_obs"]
                xi, eta = sky_to_tangent(exp["_true_center_ra_deg"], exp["_true_center_dec_deg"], det_ra, det_dec)
                theta = exp["_true_rotation_deg"] * DEG
                dx = (math.cos(theta) * xi + math.sin(theta) * eta) / exp["_true_scale_arcsec_px"]
                dy = (-math.sin(theta) * xi + math.cos(theta) * eta) / exp["_true_scale_arcsec_px"]
                fac = 1.0 + exp["radial_k"] * (dx * dx + dy * dy)
                writer.writerow(
                    {
                        "detection_id": f"{exp['exposure_id'][:2].upper()}D{idx:03d}",
                        "x_px": f"{exp['crpix_x'] + dx * fac:.6f}",
                        "y_px": f"{exp['crpix_y'] + dy * fac:.6f}",
                        "flux": f"{7000.0 + idx * 311.0:.3f}",
                    }
                )
            if exp["exposure_id"] == "holdout_south":
                writer.writerow(
                    {
                        "detection_id": "HSD999",
                        "x_px": f"{exp['crpix_x'] + 570.0:.6f}",
                        "y_px": f"{exp['crpix_y'] - 430.0:.6f}",
                        "flux": "8123.000",
                    }
                )


def write_high_declination_observations(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    exp = {
        "exposure_id": "polar_shear",
        "file": "polar_shear.csv",
        "center_ra_deg": 359.9817,
        "center_dec_deg": 84.6904,
        "_true_center_ra_deg": 359.9821,
        "_true_center_dec_deg": 84.6898,
        "epoch_year": 2027.6,
        "crpix_x": 1536.0,
        "crpix_y": 1512.0,
        "scale_arcsec_px": 0.7508,
        "rotation_deg": -1.35,
        "radial_k": 4.6e-8,
    }
    affine = (0.742, 0.018, 0.34, -0.012, 0.757, -0.21)
    tangent_targets = [
        (-358.0, -218.0),
        (-252.0, 146.0),
        (-104.0, -82.0),
        (-7.5, 31.0),
        (-7.1, 31.3),
        (138.0, 202.0),
        (286.0, -134.0),
        (422.0, 74.0),
    ]
    catalog = []
    for idx, (xi, eta) in enumerate(tangent_targets, 1):
        ra_obs, dec_obs = tangent_to_sky(exp["_true_center_ra_deg"], exp["_true_center_dec_deg"], xi, eta)
        pm_ra = 96.0 + idx * 31.5 if idx % 2 else -142.0 + idx * 18.75
        pm_dec = -55.0 + idx * 9.5 if idx % 3 else 83.0 - idx * 7.25
        epoch = 2016.25
        dt = exp["epoch_year"] - epoch
        ra_cat = normalize_ra(ra_obs - (pm_ra * dt / 3_600_000.0) / math.cos(dec_obs * DEG))
        dec_cat = dec_obs - pm_dec * dt / 3_600_000.0
        catalog.append(
            {
                "source_id": f"P{idx:03d}",
                "ra_deg": f"{ra_cat:.10f}",
                "dec_deg": f"{dec_cat:.10f}",
                "pm_ra_mas_yr": f"{pm_ra:.6f}",
                "pm_dec_mas_yr": f"{pm_dec:.6f}",
                "epoch_year": f"{epoch:.2f}",
                "mag": f"{13.2 + idx * 0.27:.3f}",
                "_xi": xi,
                "_eta": eta,
            }
        )
    with (root / "catalog.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["source_id", "ra_deg", "dec_deg", "pm_ra_mas_yr", "pm_dec_mas_yr", "epoch_year", "mag"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in catalog:
            writer.writerow({name: row[name] for name in fields})
    with (root / "exposures.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "exposure_id",
            "file",
            "center_ra_deg",
            "center_dec_deg",
            "epoch_year",
            "crpix_x",
            "crpix_y",
            "scale_arcsec_px",
            "rotation_deg",
            "radial_k",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow({name: f"{exp[name]:.12g}" if isinstance(exp[name], float) else exp[name] for name in fields})
    a, b, c, d, e, f0 = affine
    det = a * e - b * d
    with (root / exp["file"]).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["detection_id", "x_px", "y_px", "flux"])
        writer.writeheader()
        for idx, row in enumerate(catalog[2:] + catalog[:2], 1):
            xi = float(row["_xi"])
            eta = float(row["_eta"])
            dx = (e * (xi - c) - b * (eta - f0)) / det
            dy = (-d * (xi - c) + a * (eta - f0)) / det
            fac = 1.0 + exp["radial_k"] * (dx * dx + dy * dy)
            writer.writerow(
                {
                    "detection_id": f"PSD{idx:03d}",
                    "x_px": f"{exp['crpix_x'] + dx * fac:.6f}",
                    "y_px": f"{exp['crpix_y'] + dy * fac:.6f}",
                    "flux": f"{6200.0 + idx * 257.0:.3f}",
                }
            )
        writer.writerow(
            {
                "detection_id": "PSD999",
                "x_px": f"{exp['crpix_x'] + 688.0:.6f}",
                "y_px": f"{exp['crpix_y'] - 611.0:.6f}",
                "flux": "5044.000",
            }
        )


def write_compound_observations(root: Path, exposure_ids: set[str] | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    exposures = [
        {
            "exposure_id": "compound_wrap_shear",
            "file": "compound_wrap_shear.csv",
            "center_ra_deg": 359.9548,
            "center_dec_deg": 66.4027,
            "_true_center_ra_deg": 359.9561,
            "_true_center_dec_deg": 66.4019,
            "epoch_year": 2028.3,
            "crpix_x": 1975.0,
            "crpix_y": 2088.0,
            "scale_arcsec_px": 0.638,
            "rotation_deg": -74.0,
            "radial_k": 5.2e-8,
            "affine": (0.612, -0.044, 0.71, 0.033, 0.647, -0.58),
            "targets": [
                (-332.0, 214.0),
                (-218.0, -166.0),
                (-41.0, 24.0),
                (-40.55, 24.18),
                (86.0, -208.0),
                (171.0, 145.0),
                (308.0, -63.0),
                (426.0, 232.0),
                (552.0, -194.0),
            ],
            "spurious": (612.0, -355.0),
        },
        {
            "exposure_id": "compound_polar_flip",
            "file": "compound_polar_flip.csv",
            "center_ra_deg": 142.3116,
            "center_dec_deg": -86.7424,
            "_true_center_ra_deg": 142.3109,
            "_true_center_dec_deg": -86.7418,
            "epoch_year": 2029.1,
            "crpix_x": 1610.0,
            "crpix_y": 1498.0,
            "scale_arcsec_px": 0.704,
            "rotation_deg": 171.0,
            "radial_k": -3.9e-8,
            "affine": (-0.690, 0.027, -0.46, 0.035, -0.721, 0.52),
            "targets": [
                (-284.0, -196.0),
                (-172.0, 118.0),
                (-18.4, -42.0),
                (-17.9, -41.7),
                (92.0, 185.0),
                (204.0, -137.0),
                (319.0, 72.0),
                (467.0, -222.0),
                (533.0, 144.0),
            ],
            "spurious": (-640.0, 410.0),
        },
    ]

    selected_exposures = [
        exp for exp in exposures if exposure_ids is None or exp["exposure_id"] in exposure_ids
    ]
    assert selected_exposures, "compound exposure filter selected no exposures"

    catalog = []
    for exp in exposures:
        for idx, (xi, eta) in enumerate(exp["targets"], 1):
            ra_obs, dec_obs = tangent_to_sky(exp["_true_center_ra_deg"], exp["_true_center_dec_deg"], xi, eta)
            pm_ra = (180.0 - idx * 17.5) if idx % 2 else (-210.0 + idx * 23.0)
            pm_dec = (-95.0 + idx * 13.25) if idx % 3 else (122.0 - idx * 11.0)
            epoch = 2014.75
            dt = exp["epoch_year"] - epoch
            ra_cat = normalize_ra(ra_obs - (pm_ra * dt / 3_600_000.0) / math.cos(dec_obs * DEG))
            dec_cat = dec_obs - pm_dec * dt / 3_600_000.0
            prefix = "CW" if exp["exposure_id"].endswith("shear") else "CP"
            catalog.append(
                {
                    "source_id": f"{prefix}{idx:03d}",
                    "ra_deg": f"{ra_cat:.10f}",
                    "dec_deg": f"{dec_cat:.10f}",
                    "pm_ra_mas_yr": f"{pm_ra:.6f}",
                    "pm_dec_mas_yr": f"{pm_dec:.6f}",
                    "epoch_year": f"{epoch:.2f}",
                    "mag": f"{11.7 + idx * 0.33:.3f}",
                    "_exp": exp,
                    "_xi": xi,
                    "_eta": eta,
                }
            )

    fields = ["source_id", "ra_deg", "dec_deg", "pm_ra_mas_yr", "pm_dec_mas_yr", "epoch_year", "mag"]
    with (root / "catalog.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in catalog:
            writer.writerow({name: row[name] for name in fields})

    exposure_fields = [
        "exposure_id",
        "file",
        "center_ra_deg",
        "center_dec_deg",
        "epoch_year",
        "crpix_x",
        "crpix_y",
        "scale_arcsec_px",
        "rotation_deg",
        "radial_k",
    ]
    with (root / "exposures.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=exposure_fields)
        writer.writeheader()
        for exp in selected_exposures:
            writer.writerow({name: f"{exp[name]:.12g}" if isinstance(exp[name], float) else exp[name] for name in exposure_fields})

    for exp in selected_exposures:
        a, b, c, d, e, f0 = exp["affine"]
        det = a * e - b * d
        rows = [row for row in catalog if row["_exp"] is exp]
        rows = rows[3:] + rows[:3]
        with (root / exp["file"]).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["detection_id", "x_px", "y_px", "flux"])
            writer.writeheader()
            for idx, row in enumerate(rows, 1):
                xi = float(row["_xi"])
                eta = float(row["_eta"])
                dx = (e * (xi - c) - b * (eta - f0)) / det
                dy = (-d * (xi - c) + a * (eta - f0)) / det
                fac = 1.0 + exp["radial_k"] * (dx * dx + dy * dy)
                writer.writerow(
                    {
                        "detection_id": f"{exp['exposure_id'][:3].upper()}{idx:03d}",
                        "x_px": f"{exp['crpix_x'] + dx * fac:.6f}",
                        "y_px": f"{exp['crpix_y'] + dy * fac:.6f}",
                        "flux": f"{8100.0 + idx * 199.0:.3f}",
                    }
                )
            sx, sy = exp["spurious"]
            writer.writerow(
                {
                    "detection_id": f"{exp['exposure_id'][:3].upper()}999",
                    "x_px": f"{exp['crpix_x'] + sx:.6f}",
                    "y_px": f"{exp['crpix_y'] + sy:.6f}",
                    "flux": "4555.000",
                }
            )


def write_tiebreak_observations(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    exp = {
        "exposure_id": "row_order_tiebreak",
        "file": "row_order_tiebreak.csv",
        "center_ra_deg": 212.125,
        "center_dec_deg": -12.75,
        "epoch_year": 2026.5,
        "crpix_x": 1200.0,
        "crpix_y": 1400.0,
        "scale_arcsec_px": 1.0,
        "rotation_deg": 0.0,
        "radial_k": 0.0,
    }
    offsets = [(-240.0, -150.0), (-80.0, 190.0), (160.0, -120.0), (300.0, 210.0)]
    catalog = []
    for idx, (xi, eta) in enumerate(offsets, 1):
        ra, dec = tangent_to_sky(exp["center_ra_deg"], exp["center_dec_deg"], xi, eta)
        catalog.append(
            {
                "source_id": f"T{idx:03d}",
                "ra_deg": f"{ra:.10f}",
                "dec_deg": f"{dec:.10f}",
                "pm_ra_mas_yr": "0.000000",
                "pm_dec_mas_yr": "0.000000",
                "epoch_year": f"{exp['epoch_year']:.2f}",
                "mag": f"{12.0 + idx * 0.2:.3f}",
                "_xi": xi,
                "_eta": eta,
            }
        )
    with (root / "catalog.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["source_id", "ra_deg", "dec_deg", "pm_ra_mas_yr", "pm_dec_mas_yr", "epoch_year", "mag"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in catalog:
            writer.writerow({name: row[name] for name in fields})
    with (root / "exposures.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["exposure_id", "file", "center_ra_deg", "center_dec_deg", "epoch_year", "crpix_x", "crpix_y", "scale_arcsec_px", "rotation_deg", "radial_k"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow({name: f"{exp[name]:.12g}" if isinstance(exp[name], float) else exp[name] for name in fields})
    with (root / exp["file"]).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["detection_id", "x_px", "y_px", "flux"])
        writer.writeheader()
        for prefix in ("ZD", "AD"):
            for idx, row in enumerate(catalog, 1):
                writer.writerow(
                    {
                        "detection_id": f"{prefix}{idx:03d}",
                        "x_px": f"{exp['crpix_x'] + float(row['_xi']):.6f}",
                        "y_px": f"{exp['crpix_y'] + float(row['_eta']):.6f}",
                        "flux": f"{5000.0 + idx * 100.0:.3f}",
                    }
                )


def write_radial_assignment_observations(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    exp = {
        "exposure_id": "radial_assignment",
        "file": "radial_assignment.csv",
        "center_ra_deg": 123.0,
        "center_dec_deg": 22.0,
        "epoch_year": 2027.0,
        "crpix_x": 1000.0,
        "crpix_y": 1000.0,
        "scale_arcsec_px": 1.0,
        "rotation_deg": 0.0,
        "radial_k": 5.0e-7,
    }
    true_offsets = [(500.0, 0.0), (650.0, 0.0), (0.0, 520.0), (-480.0, -80.0)]
    decoy_offsets = []
    detections = []
    for idx, (dx, dy) in enumerate(true_offsets, 1):
        observed_factor = 1.0 + exp["radial_k"] * (dx * dx + dy * dy)
        obs_dx = dx * observed_factor
        obs_dy = dy * observed_factor
        wrong_factor = 1.0 + exp["radial_k"] * (obs_dx * obs_dx + obs_dy * obs_dy)
        decoy_offsets.append((obs_dx * wrong_factor, obs_dy * wrong_factor))
        detections.append((f"RD{idx:03d}", obs_dx, obs_dy))
    catalog = []
    for prefix, offsets in (("S", true_offsets), ("W", decoy_offsets)):
        for idx, (xi, eta) in enumerate(offsets, 1):
            ra, dec = tangent_to_sky(exp["center_ra_deg"], exp["center_dec_deg"], xi, eta)
            catalog.append(
                {
                    "source_id": f"{prefix}{idx:03d}",
                    "ra_deg": f"{ra:.10f}",
                    "dec_deg": f"{dec:.10f}",
                    "pm_ra_mas_yr": "0.000000",
                    "pm_dec_mas_yr": "0.000000",
                    "epoch_year": f"{exp['epoch_year']:.2f}",
                    "mag": f"{13.0 + idx * 0.17:.3f}",
                }
            )
    with (root / "catalog.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["source_id", "ra_deg", "dec_deg", "pm_ra_mas_yr", "pm_dec_mas_yr", "epoch_year", "mag"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(catalog)
    with (root / "exposures.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["exposure_id", "file", "center_ra_deg", "center_dec_deg", "epoch_year", "crpix_x", "crpix_y", "scale_arcsec_px", "rotation_deg", "radial_k"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow({name: f"{exp[name]:.12g}" if isinstance(exp[name], float) else exp[name] for name in fields})
    with (root / exp["file"]).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["detection_id", "x_px", "y_px", "flux"])
        writer.writeheader()
        for idx, (det_id, dx, dy) in enumerate(detections, 1):
            writer.writerow(
                {
                    "detection_id": det_id,
                    "x_px": f"{exp['crpix_x'] + dx:.6f}",
                    "y_px": f"{exp['crpix_y'] + dy:.6f}",
                    "flux": f"{7200.0 + idx * 150.0:.3f}",
                }
            )


def test_input_fixtures_are_unchanged():
    """Verify the bundled catalog, exposure metadata, and docs were not modified."""
    for path, expected in INPUT_HASHES.items():
        assert sha256(path) == expected, f"{path} was modified"


def test_binary_builds_release_mode():
    """Verify the Rust crate builds without requiring network-only dependencies."""
    run = subprocess.run(["cargo", "build", "--release"], cwd=APP, capture_output=True, text=True, check=False)
    assert run.returncode == 0, f"cargo build failed\nstdout={run.stdout}\nstderr={run.stderr}"
    assert BIN.exists()


def test_bundled_observations_match_reference_astrometry():
    """Verify bundled fields use spherical projection, proper motion, distortion, and matching correctly."""
    compare_products(OBS, PLATE_OUT, CAL_OUT)


def test_holdout_observations_match_reference_astrometry():
    """Verify the implementation generalizes to unseen observation directories with wraparound and distortion."""
    with tempfile.TemporaryDirectory(prefix="astrofit_holdout_") as tmp:
        root = Path(tmp) / "observations"
        write_holdout_observations(root)
        compare_products(root, Path(tmp) / "plate.csv", Path(tmp) / "calibrated.csv")


def test_high_declination_observations_match_reference_astrometry():
    """Verify high-declination fields with local shear and close source pairs."""
    with tempfile.TemporaryDirectory(prefix="astrofit_polar_") as tmp:
        root = Path(tmp) / "observations"
        write_high_declination_observations(root)
        compare_products(root, Path(tmp) / "plate.csv", Path(tmp) / "calibrated.csv")


def test_compound_wrap_shear_holdout_matches_reference_astrometry():
    """Verify wraparound, local shear, close-pair, and spurious-detection cases."""
    with tempfile.TemporaryDirectory(prefix="astrofit_compound_") as tmp:
        root = Path(tmp) / "observations"
        write_compound_observations(root, {"compound_wrap_shear"})
        compare_products(root, Path(tmp) / "plate.csv", Path(tmp) / "calibrated.csv")


def test_compound_polar_flip_holdout_matches_reference_astrometry():
    """Verify near-polar mirrored-affine, close-pair, and spurious-detection cases."""
    with tempfile.TemporaryDirectory(prefix="astrofit_compound_") as tmp:
        root = Path(tmp) / "observations"
        write_compound_observations(root, {"compound_polar_flip"})
        compare_products(root, Path(tmp) / "plate.csv", Path(tmp) / "calibrated.csv")


def test_tied_maximum_subsets_use_detection_id_order_not_row_order():
    """Verify equal-size, equal-RMS subsets break ties by detection ID, not input row order."""
    with tempfile.TemporaryDirectory(prefix="astrofit_tiebreak_") as tmp:
        root = Path(tmp) / "observations"
        write_tiebreak_observations(root)
        compare_products(root, Path(tmp) / "plate.csv", Path(tmp) / "calibrated.csv")


def test_initial_source_ranking_uses_inverse_radial_distortion():
    """Verify distorted detector coordinates are inverted before initial source assignment."""
    with tempfile.TemporaryDirectory(prefix="astrofit_radial_assignment_") as tmp:
        root = Path(tmp) / "observations"
        write_radial_assignment_observations(root)
        compare_products(root, Path(tmp) / "plate.csv", Path(tmp) / "calibrated.csv")


def test_outputs_are_byte_deterministic():
    """Verify repeated runs produce byte-identical CSV products."""
    with tempfile.TemporaryDirectory(prefix="astrofit_repeat_") as tmp:
        first_plate = Path(tmp) / "first_plate.csv"
        first_cal = Path(tmp) / "first_cal.csv"
        second_plate = Path(tmp) / "second_plate.csv"
        second_cal = Path(tmp) / "second_cal.csv"
        run_binary(OBS, first_plate, first_cal)
        run_binary(OBS, second_plate, second_cal)
        assert first_plate.read_bytes() == second_plate.read_bytes()
        assert first_cal.read_bytes() == second_cal.read_bytes()
        shutil.copy(first_plate, PLATE_OUT)
        shutil.copy(first_cal, CAL_OUT)
