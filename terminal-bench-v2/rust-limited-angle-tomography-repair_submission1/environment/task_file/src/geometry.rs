#[derive(Clone, Debug)]
pub struct Geometry {
    pub width: usize, pub height: usize, pub pixel_spacing: f64, pub origin: [f64; 2],
    pub detector_count: usize, pub detector_spacing: f64, pub sample_count: usize, pub angles_degrees: Vec<f64>,
}
impl Geometry {
    pub fn pixels(&self) -> usize { self.width * self.height }
    pub fn rays(&self) -> usize { self.detector_count * self.angles_degrees.len() }
    pub fn detector_position(&self, detector: usize) -> f64 { (detector as f64 - (self.detector_count as f64 - 1.0) * 0.5) * self.detector_spacing }
    pub fn center_of(&self, idx: usize) -> (f64, f64) {
        let x = idx % self.width; let y = idx / self.width;
        (self.origin[0] + x as f64 * self.pixel_spacing, self.origin[1] + y as f64 * self.pixel_spacing)
    }
}
