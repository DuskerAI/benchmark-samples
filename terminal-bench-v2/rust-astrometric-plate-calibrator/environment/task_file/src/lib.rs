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
use report::{write_calibrated_sources, write_plate_solutions};
use types::{CalibratedRow, PlateRow};

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
        let skies = detections
            .iter()
            .map(|detection| calibrate_detection(&exposure, detection))
            .collect::<Vec<_>>();
        let assignments = assign_sources(&skies, &propagated)
            .ok_or_else(|| format!("could not assign sources for {}", exposure.exposure_id))?;
        let mut sum_sq = 0.0;
        let mut matched = 0usize;
        for (idx, detection) in detections.iter().enumerate() {
            let sky = skies[idx];
            let best = &propagated[assignments[idx]];
            let resid = residual_arcsec(sky.ra_deg, sky.dec_deg, best.ra_deg, best.dec_deg);
            sum_sq += resid * resid;
            matched += 1;
            calibrated.push(CalibratedRow {
                exposure_id: exposure.exposure_id.clone(),
                detection_id: detection.detection_id.clone(),
                source_id: best.source_id.clone(),
                ra_deg: sky.ra_deg,
                dec_deg: sky.dec_deg,
                residual_arcsec: resid,
            });
        }
        let rms = if matched == 0 {
            0.0
        } else {
            (sum_sq / matched as f64).sqrt()
        };
        plates.push(PlateRow {
            exposure_id: exposure.exposure_id,
            matched_sources: matched,
            rms_arcsec: rms,
            center_ra_deg: exposure.center_ra_deg,
            center_dec_deg: exposure.center_dec_deg,
            scale_arcsec_px: exposure.scale_arcsec_px,
            rotation_deg: exposure.rotation_deg,
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
