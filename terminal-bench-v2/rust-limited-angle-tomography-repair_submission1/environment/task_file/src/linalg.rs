pub fn axpy(a: f64, x: &[f64], y: &mut [f64]) { for (yi, xi) in y.iter_mut().zip(x.iter()) { *yi += a * xi; } }
pub fn dot(x: &[f64], y: &[f64]) -> f64 { x.iter().zip(y.iter()).map(|(a,b)| a*b).sum() }
