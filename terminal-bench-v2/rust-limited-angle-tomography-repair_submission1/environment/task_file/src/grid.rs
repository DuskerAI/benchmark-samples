pub fn zeros(n: usize) -> Vec<f64> { vec![0.0; n] }
pub fn clamp_nonnegative(values: &mut [f64]) { for v in values { if *v < 0.0 { *v = 0.0; } } }
