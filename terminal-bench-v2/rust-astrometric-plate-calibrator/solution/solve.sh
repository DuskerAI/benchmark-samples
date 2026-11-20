#!/bin/bash
set -euo pipefail

cd /app

cat > src/catalog.rs <<'RS'
use std::path::Path;

use crate::csv::{field, parse_f64, read_csv};
use crate::math::{normalize_ra_deg, DEG_TO_RAD};
use crate::types::{CatalogSource, Detection, ExposureMeta, PropagatedSource};

impl CatalogSource {
    pub fn propagated(&self, epoch_year: f64) -> PropagatedSource {
        let dt = epoch_year - self.epoch_year;
        let cos_dec = (self.dec_deg * DEG_TO_RAD).cos();
        let ra_shift_deg = (self.pm_ra_mas_yr * dt / 3_600_000.0) / cos_dec;
        let dec_shift_deg = self.pm_dec_mas_yr * dt / 3_600_000.0;
        PropagatedSource {
            source_id: self.source_id.clone(),
            ra_deg: normalize_ra_deg(self.ra_deg + ra_shift_deg),
            dec_deg: self.dec_deg + dec_shift_deg,
        }
    }
}

pub fn load_catalog(path: &Path) -> Result<Vec<CatalogSource>, String> {
    let mut out = Vec::new();
    for row in read_csv(path)? {
        out.push(CatalogSource {
            source_id: field(&row, "source_id")?.to_string(),
            ra_deg: parse_f64(&row, "ra_deg")?,
            dec_deg: parse_f64(&row, "dec_deg")?,
            pm_ra_mas_yr: parse_f64(&row, "pm_ra_mas_yr")?,
            pm_dec_mas_yr: parse_f64(&row, "pm_dec_mas_yr")?,
            epoch_year: parse_f64(&row, "epoch_year")?,
            mag: parse_f64(&row, "mag")?,
        });
    }
    Ok(out)
}

pub fn load_exposures(path: &Path) -> Result<Vec<ExposureMeta>, String> {
    let mut out = Vec::new();
    for row in read_csv(path)? {
        out.push(ExposureMeta {
            exposure_id: field(&row, "exposure_id")?.to_string(),
            file: field(&row, "file")?.to_string(),
            center_ra_deg: parse_f64(&row, "center_ra_deg")?,
            center_dec_deg: parse_f64(&row, "center_dec_deg")?,
            epoch_year: parse_f64(&row, "epoch_year")?,
            crpix_x: parse_f64(&row, "crpix_x")?,
            crpix_y: parse_f64(&row, "crpix_y")?,
            scale_arcsec_px: parse_f64(&row, "scale_arcsec_px")?,
            rotation_deg: parse_f64(&row, "rotation_deg")?,
            radial_k: parse_f64(&row, "radial_k")?,
        });
    }
    Ok(out)
}

pub fn load_detections(path: &Path) -> Result<Vec<Detection>, String> {
    let mut out = Vec::new();
    for row in read_csv(path)? {
        out.push(Detection {
            detection_id: field(&row, "detection_id")?.to_string(),
            x_px: parse_f64(&row, "x_px")?,
            y_px: parse_f64(&row, "y_px")?,
            flux: parse_f64(&row, "flux")?,
        });
    }
    Ok(out)
}
RS

cat > src/projection.rs <<'RS'
use crate::math::{normalize_ra_deg, ARCSEC_PER_RAD, DEG_TO_RAD, RAD_TO_DEG};
use crate::types::SkyCoord;

fn wrap_radians(x: f64) -> f64 {
    (x + std::f64::consts::PI).rem_euclid(2.0 * std::f64::consts::PI) - std::f64::consts::PI
}

pub fn tangent_to_sky(center_ra_deg: f64, center_dec_deg: f64, xi_arcsec: f64, eta_arcsec: f64) -> SkyCoord {
    let alpha0 = center_ra_deg * DEG_TO_RAD;
    let delta0 = center_dec_deg * DEG_TO_RAD;
    let xi = xi_arcsec / ARCSEC_PER_RAD;
    let eta = eta_arcsec / ARCSEC_PER_RAD;
    let denom = delta0.cos() - eta * delta0.sin();
    let alpha = alpha0 + xi.atan2(denom);
    let delta = (delta0.sin() + eta * delta0.cos()).atan2((xi * xi + denom * denom).sqrt());
    SkyCoord {
        ra_deg: normalize_ra_deg(alpha * RAD_TO_DEG),
        dec_deg: delta * RAD_TO_DEG,
    }
}

pub fn sky_to_tangent(center_ra_deg: f64, center_dec_deg: f64, ra_deg: f64, dec_deg: f64) -> (f64, f64) {
    let alpha0 = center_ra_deg * DEG_TO_RAD;
    let delta0 = center_dec_deg * DEG_TO_RAD;
    let alpha = ra_deg * DEG_TO_RAD;
    let delta = dec_deg * DEG_TO_RAD;
    let da = wrap_radians(alpha - alpha0);
    let cosc = delta0.sin() * delta.sin() + delta0.cos() * delta.cos() * da.cos();
    let xi = delta.cos() * da.sin() / cosc;
    let eta = (delta0.cos() * delta.sin() - delta0.sin() * delta.cos() * da.cos()) / cosc;
    (xi * ARCSEC_PER_RAD, eta * ARCSEC_PER_RAD)
}

#[allow(dead_code)]
pub fn position_angle_deg(ra1_deg: f64, dec1_deg: f64, ra2_deg: f64, dec2_deg: f64) -> f64 {
    let da = wrap_radians((ra2_deg - ra1_deg) * DEG_TO_RAD);
    let d1 = dec1_deg * DEG_TO_RAD;
    let d2 = dec2_deg * DEG_TO_RAD;
    let y = da.sin() * d2.cos();
    let x = d1.cos() * d2.sin() - d1.sin() * d2.cos() * da.cos();
    normalize_ra_deg(y.atan2(x) * RAD_TO_DEG)
}
RS

cat > src/fit.rs <<'RS'
use crate::math::{angular_sep_arcsec, DEG_TO_RAD};
use crate::projection::tangent_to_sky;
use crate::types::{Detection, ExposureMeta, SkyCoord};

pub fn calibrate_detection(exposure: &ExposureMeta, detection: &Detection) -> SkyCoord {
    let dx_obs = detection.x_px - exposure.crpix_x;
    let dy_obs = detection.y_px - exposure.crpix_y;
    let mut dx = dx_obs;
    let mut dy = dy_obs;
    for _ in 0..8 {
        let r2 = dx * dx + dy * dy;
        let factor = 1.0 + exposure.radial_k * r2;
        dx = dx_obs / factor;
        dy = dy_obs / factor;
    }
    let theta = exposure.rotation_deg * DEG_TO_RAD;
    let xi_arcsec = exposure.scale_arcsec_px * (theta.cos() * dx - theta.sin() * dy);
    let eta_arcsec = exposure.scale_arcsec_px * (theta.sin() * dx + theta.cos() * dy);
    tangent_to_sky(
        exposure.center_ra_deg,
        exposure.center_dec_deg,
        xi_arcsec,
        eta_arcsec,
    )
}

pub fn residual_arcsec(ra1: f64, dec1: f64, ra2: f64, dec2: f64) -> f64 {
    angular_sep_arcsec(ra1, dec1, ra2, dec2)
}
RS

cat > src/matching.rs <<'RS'
use crate::math::angular_sep_arcsec;
use crate::types::{PropagatedSource, SkyCoord};

pub fn nearest_source(ra_deg: f64, dec_deg: f64, catalog: &[PropagatedSource]) -> Option<&PropagatedSource> {
    let mut best: Option<(&PropagatedSource, f64)> = None;
    for source in catalog {
        let dist = angular_sep_arcsec(ra_deg, dec_deg, source.ra_deg, source.dec_deg);
        match best {
            None => best = Some((source, dist)),
            Some((current_source, current))
                if dist < current
                    || ((dist - current).abs() < 1.0e-12 && source.source_id < current_source.source_id) =>
            {
                best = Some((source, dist));
            }
            _ => {}
        }
    }
    best.map(|(source, _)| source)
}

pub fn assign_sources(skies: &[SkyCoord], catalog: &[PropagatedSource]) -> Option<Vec<usize>> {
    if skies.len() > catalog.len() {
        return None;
    }
    let mut candidates: Vec<Vec<(f64, usize)>> = Vec::new();
    for sky in skies {
        let mut ranked = catalog
            .iter()
            .enumerate()
            .map(|(idx, source)| {
                (
                    angular_sep_arcsec(sky.ra_deg, sky.dec_deg, source.ra_deg, source.dec_deg),
                    idx,
                )
            })
            .collect::<Vec<_>>();
        ranked.sort_by(|a, b| {
            a.0.partial_cmp(&b.0)
                .unwrap()
                .then_with(|| catalog[a.1].source_id.cmp(&catalog[b.1].source_id))
        });
        ranked.truncate(ranked.len().min(8));
        candidates.push(ranked);
    }

    let mut order = (0..skies.len()).collect::<Vec<_>>();
    order.sort_by(|a, b| {
        candidates[*a][0]
            .0
            .partial_cmp(&candidates[*b][0].0)
            .unwrap()
            .then_with(|| a.cmp(b))
    });

    let mut used = vec![false; catalog.len()];
    let mut picked = vec![None; skies.len()];
    let mut best_cost = f64::INFINITY;
    let mut best = None;

    fn search(
        pos: usize,
        order: &[usize],
        candidates: &[Vec<(f64, usize)>],
        used: &mut [bool],
        picked: &mut [Option<usize>],
        total: f64,
        best_cost: &mut f64,
        best: &mut Option<Vec<usize>>,
    ) {
        if total >= *best_cost {
            return;
        }
        if pos == order.len() {
            *best_cost = total;
            *best = Some(picked.iter().map(|v| v.unwrap()).collect());
            return;
        }
        let det_idx = order[pos];
        for (dist, src_idx) in &candidates[det_idx] {
            if used[*src_idx] {
                continue;
            }
            used[*src_idx] = true;
            picked[det_idx] = Some(*src_idx);
            search(
                pos + 1,
                order,
                candidates,
                used,
                picked,
                total + *dist,
                best_cost,
                best,
            );
            picked[det_idx] = None;
            used[*src_idx] = false;
        }
    }

    search(
        0,
        &order,
        &candidates,
        &mut used,
        &mut picked,
        0.0,
        &mut best_cost,
        &mut best,
    );
    best
}
RS

cat > src/lib.rs <<'RS'
pub mod catalog;
pub mod csv;
pub mod fit;
pub mod matching;
pub mod math;
pub mod projection;
pub mod report;
pub mod types;

use std::path::Path;

use catalog::load_catalog;
use fit::{calibrate_detection, residual_arcsec};
use matching::assign_sources;
use math::RAD_TO_DEG;
use projection::{sky_to_tangent, tangent_to_sky};
use report::{write_calibrated_sources, write_plate_solutions};
use types::{CalibratedRow, Detection, ExposureMeta, PlateRow};

fn undistorted_offset(exposure: &ExposureMeta, detection: &Detection) -> (f64, f64) {
    let dx_obs = detection.x_px - exposure.crpix_x;
    let dy_obs = detection.y_px - exposure.crpix_y;
    let mut dx = dx_obs;
    let mut dy = dy_obs;
    for _ in 0..8 {
        let r2 = dx * dx + dy * dy;
        let factor = 1.0 + exposure.radial_k * r2;
        dx = dx_obs / factor;
        dy = dy_obs / factor;
    }
    (dx, dy)
}

fn solve_3x3(mut a: [[f64; 3]; 3], mut b: [f64; 3]) -> Result<[f64; 3], String> {
    for col in 0..3 {
        let mut pivot = col;
        for row in (col + 1)..3 {
            if a[row][col].abs() > a[pivot][col].abs() {
                pivot = row;
            }
        }
        if a[pivot][col].abs() < 1.0e-12 {
            return Err("singular plate fit".to_string());
        }
        if pivot != col {
            a.swap(col, pivot);
            b.swap(col, pivot);
        }
        let scale = a[col][col];
        for j in col..3 {
            a[col][j] /= scale;
        }
        b[col] /= scale;
        for row in 0..3 {
            if row == col {
                continue;
            }
            let factor = a[row][col];
            for j in col..3 {
                a[row][j] -= factor * a[col][j];
            }
            b[row] -= factor * b[col];
        }
    }
    Ok(b)
}

fn fit_affine(offsets: &[(f64, f64)], targets: &[(f64, f64)]) -> Result<[f64; 6], String> {
    let mut normal = [[0.0_f64; 3]; 3];
    let mut rhs_x = [0.0_f64; 3];
    let mut rhs_y = [0.0_f64; 3];
    for ((dx, dy), (xi, eta)) in offsets.iter().zip(targets.iter()) {
        let row = [*dx, *dy, 1.0];
        for i in 0..3 {
            rhs_x[i] += row[i] * *xi;
            rhs_y[i] += row[i] * *eta;
            for j in 0..3 {
                normal[i][j] += row[i] * row[j];
            }
        }
    }
    let x = solve_3x3(normal, rhs_x)?;
    let y = solve_3x3(normal, rhs_y)?;
    Ok([x[0], x[1], x[2], y[0], y[1], y[2]])
}

const MAX_FIT_RMS_ARCSEC: f64 = 0.05;
const MAX_FIT_RESIDUAL_ARCSEC: f64 = 0.08;

type PlateCandidate = (f64, Vec<String>, Vec<usize>, Vec<usize>, [f64; 6], Vec<f64>);

fn evaluate_subset(
    exposure: &ExposureMeta,
    skies: &[types::SkyCoord],
    offsets: &[(f64, f64)],
    propagated: &[types::PropagatedSource],
    detection_ids: &[String],
    indices: &[usize],
) -> Option<PlateCandidate> {
    let subset_skies = indices.iter().map(|idx| skies[*idx]).collect::<Vec<_>>();
    let subset_offsets = indices.iter().map(|idx| offsets[*idx]).collect::<Vec<_>>();
    let assignments = assign_sources(&subset_skies, propagated)?;
    let targets = assignments
        .iter()
        .map(|idx| {
            let source = &propagated[*idx];
            sky_to_tangent(
                exposure.center_ra_deg,
                exposure.center_dec_deg,
                source.ra_deg,
                source.dec_deg,
            )
        })
        .collect::<Vec<_>>();
    let aff = fit_affine(&subset_offsets, &targets).ok()?;
    let [a, b, c, d, e, f] = aff;
    let mut residuals = Vec::new();
    for (pos, (dx, dy)) in subset_offsets.iter().enumerate() {
        let xi = a * *dx + b * *dy + c;
        let eta = d * *dx + e * *dy + f;
        let sky = tangent_to_sky(exposure.center_ra_deg, exposure.center_dec_deg, xi, eta);
        let source = &propagated[assignments[pos]];
        residuals.push(residual_arcsec(sky.ra_deg, sky.dec_deg, source.ra_deg, source.dec_deg));
    }
    let rms = (residuals.iter().map(|r| r * r).sum::<f64>() / residuals.len() as f64).sqrt();
    let max_residual = residuals.iter().copied().fold(0.0_f64, f64::max);
    if rms <= MAX_FIT_RMS_ARCSEC && max_residual <= MAX_FIT_RESIDUAL_ARCSEC {
        let tie_ids = indices
            .iter()
            .map(|idx| detection_ids[*idx].clone())
            .collect::<Vec<_>>();
        Some((rms, tie_ids, indices.to_vec(), assignments, aff, residuals))
    } else {
        None
    }
}

fn search_subsets(
    exposure: &ExposureMeta,
    skies: &[types::SkyCoord],
    offsets: &[(f64, f64)],
    propagated: &[types::PropagatedSource],
    detection_ids: &[String],
    size: usize,
    start: usize,
    current: &mut Vec<usize>,
    best: &mut Option<PlateCandidate>,
) {
    if current.len() == size {
        if let Some(candidate) = evaluate_subset(exposure, skies, offsets, propagated, detection_ids, current) {
            let replace = match best {
                None => true,
                Some(existing) => {
                    candidate.0 < existing.0
                        || ((candidate.0 - existing.0).abs() < 1.0e-12 && candidate.1 < existing.1)
                }
            };
            if replace {
                *best = Some(candidate);
            }
        }
        return;
    }
    let remaining = size - current.len();
    if skies.len() - start < remaining {
        return;
    }
    for idx in start..=skies.len() - remaining {
        current.push(idx);
        search_subsets(exposure, skies, offsets, propagated, detection_ids, size, idx + 1, current, best);
        current.pop();
    }
}

fn best_exposure_solution(
    exposure: &ExposureMeta,
    skies: &[types::SkyCoord],
    offsets: &[(f64, f64)],
    propagated: &[types::PropagatedSource],
    detection_ids: &[String],
) -> Option<PlateCandidate> {
    let max_size = skies.len().min(propagated.len());
    for size in (3..=max_size).rev() {
        let mut best = None;
        let mut current = Vec::new();
        search_subsets(
            exposure,
            skies,
            offsets,
            propagated,
            detection_ids,
            size,
            0,
            &mut current,
            &mut best,
        );
        if best.is_some() {
            return best;
        }
    }
    None
}

pub fn run(obs_dir: &Path, plate_out: &Path, calibrated_out: &Path) -> Result<(), String> {
    let catalog = load_catalog(&obs_dir.join("catalog.csv"))?;
    let exposures = catalog::load_exposures(&obs_dir.join("exposures.csv"))?;
    let mut calibrated = Vec::new();
    let mut plates = Vec::new();

    for exposure in exposures {
        let detections = catalog::load_detections(&obs_dir.join(&exposure.file))?;
        let propagated = catalog
            .iter()
            .map(|src| src.propagated(exposure.epoch_year))
            .collect::<Vec<_>>();
        let initial_skies = detections
            .iter()
            .map(|detection| calibrate_detection(&exposure, detection))
            .collect::<Vec<_>>();
        let offsets = detections
            .iter()
            .map(|detection| undistorted_offset(&exposure, detection))
            .collect::<Vec<_>>();
        let detection_ids = detections
            .iter()
            .map(|detection| detection.detection_id.clone())
            .collect::<Vec<_>>();
        let (_fit_rms, _tie_ids, accepted_indices, assignments, aff, residuals) =
            best_exposure_solution(&exposure, &initial_skies, &offsets, &propagated, &detection_ids)
                .ok_or_else(|| format!("could not find robust plate solution for {}", exposure.exposure_id))?;
        let [a, b, c, d, e, f] = aff;

        let mut sum_sq = 0.0;
        for (pos, idx) in accepted_indices.iter().enumerate() {
            let detection = &detections[*idx];
            let (dx, dy) = offsets[*idx];
            let xi = a * dx + b * dy + c;
            let eta = d * dx + e * dy + f;
            let sky = tangent_to_sky(exposure.center_ra_deg, exposure.center_dec_deg, xi, eta);
            let best = &propagated[assignments[pos]];
            let resid = residuals[pos];
            sum_sq += resid * resid;
            calibrated.push(CalibratedRow {
                exposure_id: exposure.exposure_id.clone(),
                detection_id: detection.detection_id.clone(),
                source_id: best.source_id.clone(),
                ra_deg: sky.ra_deg,
                dec_deg: sky.dec_deg,
                residual_arcsec: resid,
            });
        }
        let center = tangent_to_sky(exposure.center_ra_deg, exposure.center_dec_deg, c, f);
        let scale = (a * e - b * d).abs().sqrt();
        let rotation = d.atan2(a) * RAD_TO_DEG;
        let rms = (sum_sq / accepted_indices.len() as f64).sqrt();
        plates.push(PlateRow {
            exposure_id: exposure.exposure_id,
            matched_sources: accepted_indices.len(),
            rms_arcsec: rms,
            center_ra_deg: center.ra_deg,
            center_dec_deg: center.dec_deg,
            scale_arcsec_px: scale,
            rotation_deg: rotation,
        });
    }

    calibrated.sort_by(|a, b| {
        a.exposure_id
            .cmp(&b.exposure_id)
            .then_with(|| a.detection_id.cmp(&b.detection_id))
    });
    plates.sort_by(|a, b| a.exposure_id.cmp(&b.exposure_id));

    write_plate_solutions(plate_out, &plates)?;
    write_calibrated_sources(calibrated_out, &calibrated)?;
    Ok(())
}
RS

cargo build --release
