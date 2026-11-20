pub mod constants;
pub mod error;
pub mod filters;
pub mod geometry;
pub mod grid;
pub mod interp;
pub mod io;
pub mod json;
pub mod linalg;
pub mod metrics;
pub mod output;
pub mod ray;
pub mod recon;
pub mod stats;
pub mod validation;

use std::path::PathBuf;
use crate::error::TomoError;

pub fn run_from_args(args: Vec<String>) -> Result<(), TomoError> {
    if args.len() != 2 {
        return Err(TomoError::Message("usage: tomo_repair <data-dir> <output-json>".to_string()));
    }
    let data_dir = PathBuf::from(&args[0]);
    let output = PathBuf::from(&args[1]);
    let problem = io::load_problem(&data_dir)?;
    validation::validate_problem(&problem)?;
    let values = recon::reconstruct(&problem);
    let summary = metrics::summarize(&problem, &values);
    output::write_reconstruction(&output, &problem.geometry, &values, &summary)?;
    Ok(())
}
