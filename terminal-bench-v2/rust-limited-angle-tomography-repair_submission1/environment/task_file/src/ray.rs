use crate::geometry::Geometry;

pub fn ray_weights(g: &Geometry, angle_degrees: f64, detector: usize) -> Vec<(usize, f64)> {
    let theta = angle_degrees.to_radians();
    let (nx, ny) = (theta.cos(), theta.sin());
    let (tx, ty) = (-theta.sin(), theta.cos());
    let s = g.detector_position(detector);
    let half = (g.width.max(g.height) as f64) * g.pixel_spacing * 0.76;
    let ds = (2.0 * half) / ((g.sample_count - 1) as f64);
    let mut weights = vec![0.0; g.pixels()];
    for k in 0..g.sample_count {
        let u = -half + k as f64 * ds;
        let x = s * nx + u * tx;
        let y = s * ny + u * ty;
        let ix = ((x - g.origin[0]) / g.pixel_spacing).round() as isize;
        let iy = ((y - g.origin[1]) / g.pixel_spacing).round() as isize;
        if ix >= 0 && iy >= 0 && (ix as usize) < g.width && (iy as usize) < g.height { weights[iy as usize * g.width + ix as usize] += ds; }
    }
    weights.into_iter().enumerate().filter_map(|(i, w)| if w > 0.0 { Some((i, w)) } else { None }).collect()
}

pub fn project(g: &Geometry, values: &[f64]) -> Vec<f64> {
    let mut out = Vec::with_capacity(g.rays());
    for &angle in &g.angles_degrees {
        for d in 0..g.detector_count {
            out.push(ray_weights(g, angle, d).into_iter().map(|(idx, w)| values[idx] * w).sum());
        }
    }
    out
}
