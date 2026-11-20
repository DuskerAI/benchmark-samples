use std::collections::HashMap;
use std::fs;
use std::path::Path;

pub fn read_csv(path: &Path) -> Result<Vec<HashMap<String, String>>, String> {
    let text = fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut lines = text.lines().filter(|line| !line.trim().is_empty());
    let header_line = lines
        .next()
        .ok_or_else(|| format!("{} is empty", path.display()))?;
    let headers = split_csv_line(header_line);
    let mut rows = Vec::new();
    for (idx, line) in lines.enumerate() {
        let fields = split_csv_line(line);
        if fields.len() != headers.len() {
            return Err(format!(
                "{} line {} has {} fields, expected {}",
                path.display(),
                idx + 2,
                fields.len(),
                headers.len()
            ));
        }
        let mut row = HashMap::new();
        for (key, value) in headers.iter().zip(fields) {
            row.insert(key.clone(), value);
        }
        rows.push(row);
    }
    Ok(rows)
}

fn split_csv_line(line: &str) -> Vec<String> {
    line.split(',').map(|s| s.trim().to_string()).collect()
}

pub fn field<'a>(row: &'a HashMap<String, String>, name: &str) -> Result<&'a str, String> {
    row.get(name)
        .map(String::as_str)
        .ok_or_else(|| format!("missing column {name}"))
}

pub fn parse_f64(row: &HashMap<String, String>, name: &str) -> Result<f64, String> {
    field(row, name)?
        .parse::<f64>()
        .map_err(|e| format!("invalid float in {name}: {e}"))
}
