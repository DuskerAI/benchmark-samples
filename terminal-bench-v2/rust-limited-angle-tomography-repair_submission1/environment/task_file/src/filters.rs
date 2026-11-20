pub fn three_point_smooth(values: &mut [f64], width: usize, height: usize) {
    let old = values.to_vec();
    for y in 1..height.saturating_sub(1) {
        for x in 1..width.saturating_sub(1) {
            let i = y * width + x;
            values[i] = 0.55 * old[i] + 0.1125 * (old[i-1] + old[i+1] + old[i-width] + old[i+width]);
        }
    }
}
