use crate::io::Problem;
use crate::ray;
use crate::stats;

#[derive(Clone, Debug)]
pub struct Summary { pub min: f64, pub max: f64, pub mean: f64, pub active_mean: f64, pub active_energy: f64, pub measurement_residual: f64 }

pub fn summarize(problem: &Problem, values: &[f64]) -> Summary {
    let pred = ray::project(&problem.geometry, values);
    let residual = stats::relative_l2(&pred, &problem.measurements);
    let mut active_sum = 0.0; let mut active_energy = 0.0; let mut active_n = 0.0;
    for (v, &m) in values.iter().zip(problem.mask.iter()) { if m != 0 { active_sum += *v; active_energy += v * v; active_n += 1.0; } }
    Summary { min: stats::min(values), max: stats::max(values), mean: stats::mean(values), active_mean: if active_n > 0.0 { active_sum / active_n } else { 0.0 }, active_energy, measurement_residual: residual }
}
