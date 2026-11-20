#!/bin/bash
set -euo pipefail

export PATH="/usr/local/cargo/bin:$PATH"

cd /app

cat > src/io.rs <<'RS'
use std::fs;
use std::path::Path;
use crate::error::TomoError;
use crate::geometry::Geometry;
use crate::json;

#[derive(Clone, Debug)]
pub struct Problem { pub geometry: Geometry, pub measurements: Vec<f64>, pub mask: Vec<u8> }

fn number_for_key(text: &str, key: &str) -> Option<f64> {
    let marker = format!("\"{}\"", key);
    let start = text.find(&marker)?;
    let after = &text[start + marker.len()..];
    let colon = after.find(':')?;
    let mut buf = String::new();
    let mut in_number = false;
    for ch in after[colon + 1..].chars() {
        let numeric = ch.is_ascii_digit() || matches!(ch, '-' | '+' | '.' | 'e' | 'E');
        if numeric {
            buf.push(ch);
            in_number = true;
        } else if in_number {
            break;
        }
    }
    if in_number { buf.parse::<f64>().ok() } else { None }
}

fn array_for_key(text: &str, key: &str) -> Option<Vec<f64>> {
    let marker = format!("\"{}\"", key);
    let start = text.find(&marker)?;
    let after = &text[start + marker.len()..];
    let colon = after.find(':')?;
    let value = after[colon + 1..].trim_start();
    if !value.starts_with('[') {
        return None;
    }
    let end = value.find(']')?;
    Some(json::numbers(&value[..=end]))
}

fn calibration_values(text: &str, key: &str, default: f64, detectors: usize) -> Result<Vec<f64>, TomoError> {
    if let Some(values) = array_for_key(text, key) {
        if values.len() != detectors {
            return Err(TomoError::Message(format!("{} length does not match detector_count", key)));
        }
        if values.iter().any(|v| !v.is_finite()) {
            return Err(TomoError::Message(format!("{} contains non-finite calibration", key)));
        }
        return Ok(values);
    }
    Ok(vec![number_for_key(text, key).unwrap_or(default); detectors])
}

fn calibration(data_dir: &Path, detectors: usize) -> Result<(Vec<f64>, Vec<f64>), TomoError> {
    let path = data_dir.join("calibration.json");
    if !path.exists() {
        return Ok((vec![1.0; detectors], vec![0.0; detectors]));
    }
    let text = fs::read_to_string(path)?;
    let gain = calibration_values(&text, "detector_gain", 1.0, detectors)?;
    let dark = calibration_values(&text, "dark_current", 0.0, detectors)?;
    if gain.iter().any(|g| g.abs() <= 1.0e-12) {
        return Err(TomoError::Message("invalid numeric calibration".into()));
    }
    Ok((gain, dark))
}

pub fn load_problem(data_dir: &Path) -> Result<Problem, TomoError> {
    let geom_text = fs::read_to_string(data_dir.join("geometry.json"))?;
    let nums = json::numbers(&geom_text);
    if nums.len() < 8 { return Err(TomoError::Message("geometry.json is incomplete".into())); }
    let geometry = Geometry {
        width: nums[0] as usize, height: nums[1] as usize, pixel_spacing: nums[2], origin: [nums[3], nums[4]],
        detector_count: nums[5] as usize, detector_spacing: nums[6], sample_count: nums[7] as usize, angles_degrees: nums[8..].to_vec(),
    };
    let raw = json::numbers(&fs::read_to_string(data_dir.join("sinogram.json"))?);
    let (gain, dark) = calibration(data_dir, geometry.detector_count)?;
    let measurements = raw.into_iter().enumerate().map(|(i, v)| {
        let d = i % geometry.detector_count;
        (v - dark[d]) / gain[d]
    }).collect();
    let mask = json::numbers(&fs::read_to_string(data_dir.join("mask.json"))?).into_iter().map(|v| if v > 0.5 { 1 } else { 0 }).collect();
    Ok(Problem { geometry, measurements, mask })
}
RS

cat > src/recon.rs <<'RS'
use crate::grid;
use crate::io::Problem;
use crate::ray;

const ITERATIONS: usize = 96;
const RELAXATION: f64 = 0.28;

pub fn reconstruct(problem: &Problem) -> Vec<f64> {
    let g = &problem.geometry;
    let mut image = grid::zeros(g.pixels());
    let mut coverage = vec![0.0; g.pixels()];
    let mut rays: Vec<Vec<(usize, f64)>> = Vec::with_capacity(g.rays());

    for &angle in &g.angles_degrees {
        for d in 0..g.detector_count {
            let weights = ray::ray_weights(g, angle, d);
            for &(idx, w) in &weights {
                coverage[idx] += w * w;
            }
            rays.push(weights);
        }
    }

    for c in &mut coverage {
        if *c < 1.0e-12 {
            *c = 1.0;
        }
    }

    for _ in 0..ITERATIONS {
        let mut update = vec![0.0; g.pixels()];
        for (r, weights) in rays.iter().enumerate() {
            let mut pred = 0.0;
            let mut norm = 0.0;
            for &(idx, w) in weights {
                pred += image[idx] * w;
                norm += w * w;
            }
            if norm <= 1.0e-12 {
                continue;
            }
            let corr = (problem.measurements[r] - pred) / norm;
            for &(idx, w) in weights {
                update[idx] += corr * w;
            }
        }

        for i in 0..image.len() {
            image[i] += RELAXATION * update[i] / coverage[i].sqrt().max(1.0);
            if image[i] < 0.0 {
                image[i] = 0.0;
            }
            if problem.mask[i] == 0 {
                image[i] = 0.0;
            }
        }
    }

    image
}
RS

cargo build --release --offline
mkdir -p /app/output
/app/target/release/tomo_repair /app/data /app/output/reconstruction.json
test -f /app/output/reconstruction.json
