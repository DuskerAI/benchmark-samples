use crate::math::angular_sep_arcsec;
use crate::types::{PropagatedSource, SkyCoord};

pub fn nearest_source(ra_deg: f64, dec_deg: f64, catalog: &[PropagatedSource]) -> Option<&PropagatedSource> {
    let mut best: Option<(&PropagatedSource, f64)> = None;
    for source in catalog {
        let dist = angular_sep_arcsec(ra_deg, dec_deg, source.ra_deg, source.dec_deg);
        match best {
            None => best = Some((source, dist)),
            Some((_, current)) if dist < current => best = Some((source, dist)),
            _ => {}
        }
    }
    best.map(|(source, _)| source)
}

pub fn assign_sources(skies: &[SkyCoord], catalog: &[PropagatedSource]) -> Option<Vec<usize>> {
    let mut assigned = Vec::new();
    for sky in skies {
        let best = nearest_source(sky.ra_deg, sky.dec_deg, catalog)?;
        let idx = catalog
            .iter()
            .position(|source| source.source_id == best.source_id)?;
        assigned.push(idx);
    }
    Some(assigned)
}
