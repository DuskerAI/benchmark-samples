pub const DEG_TO_RAD: f64 = std::f64::consts::PI / 180.0;
pub const RAD_TO_DEG: f64 = 180.0 / std::f64::consts::PI;
pub const ARCSEC_PER_RAD: f64 = 206_264.806_247_096_36;

pub fn normalize_ra_deg(mut ra: f64) -> f64 {
    while ra < 0.0 {
        ra += 360.0;
    }
    while ra >= 360.0 {
        ra -= 360.0;
    }
    ra
}

pub fn angular_sep_arcsec(ra1_deg: f64, dec1_deg: f64, ra2_deg: f64, dec2_deg: f64) -> f64 {
    let dra = (ra2_deg - ra1_deg) * DEG_TO_RAD;
    let d1 = dec1_deg * DEG_TO_RAD;
    let d2 = dec2_deg * DEG_TO_RAD;
    let s = d1.sin() * d2.sin() + d1.cos() * d2.cos() * dra.cos();
    s.clamp(-1.0, 1.0).acos() * ARCSEC_PER_RAD
}
