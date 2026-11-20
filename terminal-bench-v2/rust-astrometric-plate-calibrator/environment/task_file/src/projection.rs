use crate::math::{normalize_ra_deg, ARCSEC_PER_RAD, DEG_TO_RAD, RAD_TO_DEG};
use crate::types::SkyCoord;

pub fn tangent_to_sky(center_ra_deg: f64, center_dec_deg: f64, xi_arcsec: f64, eta_arcsec: f64) -> SkyCoord {
    let ra = center_ra_deg + xi_arcsec / 3600.0 / (center_dec_deg * DEG_TO_RAD).cos();
    let dec = center_dec_deg + eta_arcsec / 3600.0;
    SkyCoord {
        ra_deg: normalize_ra_deg(ra),
        dec_deg: dec,
    }
}

pub fn sky_to_tangent(center_ra_deg: f64, center_dec_deg: f64, ra_deg: f64, dec_deg: f64) -> (f64, f64) {
    let xi = (ra_deg - center_ra_deg) * DEG_TO_RAD * (center_dec_deg * DEG_TO_RAD).cos();
    let eta = (dec_deg - center_dec_deg) * DEG_TO_RAD;
    (xi * ARCSEC_PER_RAD, eta * ARCSEC_PER_RAD)
}

#[allow(dead_code)]
pub fn position_angle_deg(ra1_deg: f64, dec1_deg: f64, ra2_deg: f64, dec2_deg: f64) -> f64 {
    let y = (ra2_deg - ra1_deg).to_radians().sin() * dec2_deg.to_radians().cos();
    let x = dec1_deg.to_radians().cos() * dec2_deg.to_radians().sin()
        - dec1_deg.to_radians().sin() * dec2_deg.to_radians().cos() * (ra2_deg - ra1_deg).to_radians().cos();
    normalize_ra_deg(y.atan2(x) * RAD_TO_DEG)
}
