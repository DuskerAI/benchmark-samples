use std::fs;
use std::io::Write;
use std::path::Path;

use crate::types::{CalibratedRow, PlateRow};

pub fn write_plate_solutions(path: &Path, rows: &[PlateRow]) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
    }
    let mut file = fs::File::create(path).map_err(|e| format!("{}: {e}", path.display()))?;
    writeln!(
        file,
        "exposure_id,matched_sources,rms_arcsec,center_ra_deg,center_dec_deg,scale_arcsec_px,rotation_deg"
    )
    .map_err(|e| e.to_string())?;
    for row in rows {
        writeln!(
            file,
            "{},{},{:.9},{:.10},{:.10},{:.10},{:.10}",
            row.exposure_id,
            row.matched_sources,
            row.rms_arcsec,
            row.center_ra_deg,
            row.center_dec_deg,
            row.scale_arcsec_px,
            row.rotation_deg
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn write_calibrated_sources(path: &Path, rows: &[CalibratedRow]) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
    }
    let mut file = fs::File::create(path).map_err(|e| format!("{}: {e}", path.display()))?;
    writeln!(
        file,
        "exposure_id,detection_id,source_id,ra_deg,dec_deg,residual_arcsec"
    )
    .map_err(|e| e.to_string())?;
    for row in rows {
        writeln!(
            file,
            "{},{},{},{:.10},{:.10},{:.9}",
            row.exposure_id,
            row.detection_id,
            row.source_id,
            row.ra_deg,
            row.dec_deg,
            row.residual_arcsec
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}
