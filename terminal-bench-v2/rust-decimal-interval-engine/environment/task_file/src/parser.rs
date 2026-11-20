use crate::decimal::{self, Interval};
use std::collections::BTreeMap;

#[derive(Clone, Debug)]
pub struct Factor {
    pub bucket: String,
    pub variance: Interval,
}

#[derive(Clone, Debug)]
pub struct Position {
    pub factor: String,
    pub amount: Interval,
    pub sensitivity: Interval,
}

#[derive(Clone, Debug)]
pub struct Portfolio {
    pub name: String,
    pub scale: u32,
    pub factors: BTreeMap<String, Factor>,
    pub factor_order: Vec<String>,
    pub positions: Vec<Position>,
    pub covariances: BTreeMap<(String, String), Interval>,
}

pub fn parse_portfolio(text: &str) -> Result<Portfolio, String> {
    let mut name: Option<String> = None;
    let mut scale: Option<u32> = None;
    let mut factors = BTreeMap::new();
    let mut factor_order = Vec::new();
    let mut positions = Vec::new();
    let mut covariances = BTreeMap::new();

    for (idx, raw) in text.lines().enumerate() {
        let line_no = idx + 1;
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        match parts.as_slice() {
            ["portfolio", n] => {
                if name.is_some() {
                    return Err(format!("line {line_no}: duplicate portfolio"));
                }
                check_name(n)?;
                name = Some((*n).to_string());
            }
            ["scale", s] => {
                if scale.is_some() {
                    return Err(format!("line {line_no}: duplicate scale"));
                }
                let parsed: u32 = s.parse().map_err(|_| format!("line {line_no}: bad scale"))?;
                if parsed > 6 {
                    return Err(format!("line {line_no}: bad scale"));
                }
                scale = Some(parsed);
            }
            ["factor", factor, bucket, var] => {
                let sc = scale.ok_or_else(|| format!("line {line_no}: missing scale"))?;
                check_name(factor)?;
                check_name(bucket)?;
                if factors.contains_key(*factor) {
                    return Err(format!("line {line_no}: duplicate factor"));
                }
                factor_order.push((*factor).to_string());
                factors.insert(
                    (*factor).to_string(),
                    Factor {
                        bucket: (*bucket).to_string(),
                        variance: decimal::parse_interval(var, sc)?,
                    },
                );
            }
            ["position", id, factor, amount, sensitivity] => {
                let sc = scale.ok_or_else(|| format!("line {line_no}: missing scale"))?;
                check_name(id)?;
                check_name(factor)?;
                if !factors.contains_key(*factor) {
                    return Err(format!("line {line_no}: unknown factor"));
                }
                positions.push(Position {
                    factor: (*factor).to_string(),
                    amount: decimal::parse_scalar(amount, sc)?,
                    sensitivity: decimal::parse_interval(sensitivity, sc)?,
                });
            }
            ["cov", a, b, value] => {
                let sc = scale.ok_or_else(|| format!("line {line_no}: missing scale"))?;
                check_name(a)?;
                check_name(b)?;
                if a == b || !factors.contains_key(*a) || !factors.contains_key(*b) {
                    return Err(format!("line {line_no}: bad covariance"));
                }
                covariances.insert(((*a).to_string(), (*b).to_string()), decimal::parse_interval(value, sc)?);
            }
            _ => return Err(format!("line {line_no}: malformed record")),
        }
    }

    Ok(Portfolio {
        name: name.ok_or_else(|| "missing portfolio".to_string())?,
        scale: scale.ok_or_else(|| "missing scale".to_string())?,
        factors,
        factor_order,
        positions,
        covariances,
    })
}

fn check_name(name: &str) -> Result<(), String> {
    let mut chars = name.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() => {}
        _ => return Err(format!("bad name {name}")),
    }
    if name.len() > 40
        || !chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == ':')
    {
        return Err(format!("bad name {name}"));
    }
    Ok(())
}
