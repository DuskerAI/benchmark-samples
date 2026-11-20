use std::path::Path;

use crate::csv::{field, parse_f64, read_csv};
use crate::math::normalize_ra_deg;
use crate::types::{CatalogSource, Detection, ExposureMeta, PropagatedSource};

impl CatalogSource {
    pub fn propagated(&self, epoch_year: f64) -> PropagatedSource {
        let dt = epoch_year - self.epoch_year;
        let ra_shift_deg = self.pm_ra_mas_yr * dt / 3_600_000.0;
        let dec_shift_deg = -self.pm_dec_mas_yr * dt / 3_600_000.0;
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
