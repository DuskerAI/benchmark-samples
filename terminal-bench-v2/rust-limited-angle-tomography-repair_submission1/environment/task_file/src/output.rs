use std::fs;
use std::path::Path;
use crate::error::TomoError;
use crate::geometry::Geometry;
use crate::metrics::Summary;

pub fn write_reconstruction(path: &Path, g: &Geometry, values: &[f64], summary: &Summary) -> Result<(), TomoError> {
    if let Some(parent) = path.parent() { fs::create_dir_all(parent)?; }
    let mut s = String::new();
    s.push_str("{\n");
    s.push_str(&format!("  \"width\": {},\n  \"height\": {},\n", g.width, g.height));
    s.push_str(&format!("  \"pixel_spacing\": {:.12},\n", g.pixel_spacing));
    s.push_str(&format!("  \"origin\": [{:.12}, {:.12}],\n", g.origin[0], g.origin[1]));
    s.push_str("  \"values\": [");
    for (i, v) in values.iter().enumerate() { if i > 0 { s.push_str(","); } if i % g.width == 0 { s.push_str("\n    "); } s.push_str(&format!("{:.15}", v)); }
    s.push_str("\n  ],\n");
    s.push_str("  \"summary\": {");
    s.push_str(&format!("\n    \"min\": {:.12},", summary.min));
    s.push_str(&format!("\n    \"max\": {:.12},", summary.max));
    s.push_str(&format!("\n    \"mean\": {:.12},", summary.mean));
    s.push_str(&format!("\n    \"active_mean\": {:.12},", summary.active_mean));
    s.push_str(&format!("\n    \"active_energy\": {:.12},", summary.active_energy));
    s.push_str(&format!("\n    \"measurement_residual\": {:.12}\n  }}\n", summary.measurement_residual));
    s.push_str("}\n");
    fs::write(path, s)?; Ok(())
}
