use crate::math::{angular_sep_arcsec, DEG_TO_RAD};
use crate::projection::tangent_to_sky;
use crate::types::{Detection, ExposureMeta, SkyCoord};

pub fn calibrate_detection(exposure: &ExposureMeta, detection: &Detection) -> SkyCoord {
    let dx = detection.x_px - exposure.crpix_x;
    let dy = detection.y_px - exposure.crpix_y;
    let r2 = dx * dx + dy * dy;
    let distortion = 1.0 + exposure.radial_k * r2;
    let distorted_x = dx * distortion;
    let distorted_y = dy * distortion;
    let theta = exposure.rotation_deg * DEG_TO_RAD;
    let xi_arcsec = exposure.scale_arcsec_px * (theta.cos() * distorted_x - theta.sin() * distorted_y);
    let eta_arcsec = exposure.scale_arcsec_px * (theta.sin() * distorted_x + theta.cos() * distorted_y);
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
