#[derive(Clone, Debug)]
pub struct CatalogSource {
    pub source_id: String,
    pub ra_deg: f64,
    pub dec_deg: f64,
    pub pm_ra_mas_yr: f64,
    pub pm_dec_mas_yr: f64,
    pub epoch_year: f64,
    pub mag: f64,
}

#[derive(Clone, Debug)]
pub struct PropagatedSource {
    pub source_id: String,
    pub ra_deg: f64,
    pub dec_deg: f64,
}

#[derive(Clone, Debug)]
pub struct ExposureMeta {
    pub exposure_id: String,
    pub file: String,
    pub center_ra_deg: f64,
    pub center_dec_deg: f64,
    pub epoch_year: f64,
    pub crpix_x: f64,
    pub crpix_y: f64,
    pub scale_arcsec_px: f64,
    pub rotation_deg: f64,
    pub radial_k: f64,
}

#[derive(Clone, Debug)]
pub struct Detection {
    pub detection_id: String,
    pub x_px: f64,
    pub y_px: f64,
    pub flux: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct SkyCoord {
    pub ra_deg: f64,
    pub dec_deg: f64,
}

#[derive(Clone, Debug)]
pub struct PlateRow {
    pub exposure_id: String,
    pub matched_sources: usize,
    pub rms_arcsec: f64,
    pub center_ra_deg: f64,
    pub center_dec_deg: f64,
    pub scale_arcsec_px: f64,
    pub rotation_deg: f64,
}

#[derive(Clone, Debug)]
pub struct CalibratedRow {
    pub exposure_id: String,
    pub detection_id: String,
    pub source_id: String,
    pub ra_deg: f64,
    pub dec_deg: f64,
    pub residual_arcsec: f64,
}
