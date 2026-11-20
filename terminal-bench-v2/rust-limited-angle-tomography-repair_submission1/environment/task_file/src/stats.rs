pub fn min(v: &[f64]) -> f64 { v.iter().copied().fold(f64::INFINITY, f64::min) }
pub fn max(v: &[f64]) -> f64 { v.iter().copied().fold(f64::NEG_INFINITY, f64::max) }
pub fn mean(v: &[f64]) -> f64 { if v.is_empty() { 0.0 } else { v.iter().sum::<f64>() / v.len() as f64 } }
pub fn relative_l2(a: &[f64], b: &[f64]) -> f64 {
    let mut num = 0.0; let mut den = 0.0;
    for (x, y) in a.iter().zip(b.iter()) { num += (x - y) * (x - y); den += y * y; }
    if den <= 0.0 { num.sqrt() } else { (num / den).sqrt() }
}
