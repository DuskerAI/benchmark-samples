use std::path::Path;

use crate::error::TomoError;
use crate::geometry::Geometry;

#[derive(Clone, Debug)]
pub struct Problem { pub geometry: Geometry, pub measurements: Vec<f64>, pub mask: Vec<u8> }

pub fn load_problem(_data_dir: &Path) -> Result<Problem, TomoError> {
    Err(TomoError::Message("scan loading is not implemented".to_string()))
}
