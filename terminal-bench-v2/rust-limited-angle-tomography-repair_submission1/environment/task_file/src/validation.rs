use crate::error::TomoError;
use crate::io::Problem;

pub fn validate_problem(problem: &Problem) -> Result<(), TomoError> {
    let g = &problem.geometry;
    if g.width == 0 || g.height == 0 || g.detector_count == 0 || g.angles_degrees.is_empty() { return Err(TomoError::Message("empty tomography geometry".into())); }
    if problem.measurements.len() != g.rays() { return Err(TomoError::Message(format!("sinogram has {} values but geometry expects {}", problem.measurements.len(), g.rays()))); }
    if problem.mask.len() != g.pixels() { return Err(TomoError::Message("mask length does not match image grid".into())); }
    Ok(())
}
