#!/bin/bash
set -euo pipefail

cd /app/task_file

cat > src/decimal.rs <<'RS'
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Interval {
    pub lo: i128,
    pub hi: i128,
}

#[derive(Clone, Copy, Debug)]
struct RawDecimal {
    num: i128,
    den: i128,
}

pub fn pow10(scale: u32) -> i128 {
    let mut value = 1i128;
    for _ in 0..scale {
        value *= 10;
    }
    value
}

pub fn zero() -> Interval {
    Interval { lo: 0, hi: 0 }
}

pub fn parse_scalar(text: &str, scale: u32) -> Result<Interval, String> {
    let raw = parse_raw(text)?;
    Ok(Interval {
        lo: raw_floor_ticks(raw, scale)?,
        hi: raw_ceil_ticks(raw, scale)?,
    })
}

pub fn parse_interval(text: &str, scale: u32) -> Result<Interval, String> {
    let trimmed = text.trim();
    let body = trimmed
        .strip_prefix('[')
        .and_then(|s| s.strip_suffix(']'))
        .ok_or_else(|| format!("bad interval {text}"))?;
    let (a, b) = body
        .split_once(',')
        .ok_or_else(|| format!("bad interval {text}"))?;
    let left = parse_raw(a.trim())?;
    let right = parse_raw(b.trim())?;
    if !raw_le(left, right)? {
        return Err(format!("invalid interval {text}"));
    }
    Ok(Interval {
        lo: raw_floor_ticks(left, scale)?,
        hi: raw_ceil_ticks(right, scale)?,
    })
}

fn parse_raw(text: &str) -> Result<RawDecimal, String> {
    let text = text.trim();
    if text.is_empty() {
        return Err("empty decimal".to_string());
    }
    let (neg, body) = match text.as_bytes()[0] {
        b'-' => (true, &text[1..]),
        b'+' => (false, &text[1..]),
        _ => (false, text),
    };
    if body.is_empty() {
        return Err(format!("bad decimal {text}"));
    }
    let (whole, frac) = body.split_once('.').unwrap_or((body, ""));
    if whole.is_empty() && frac.is_empty() {
        return Err(format!("bad decimal {text}"));
    }
    if !whole.bytes().all(|c| c.is_ascii_digit()) || !frac.bytes().all(|c| c.is_ascii_digit()) {
        return Err(format!("bad decimal {text}"));
    }
    let mut den = 1i128;
    let mut whole_num = 0i128;
    for c in whole.bytes() {
        whole_num = whole_num
            .checked_mul(10)
            .and_then(|v| v.checked_add((c - b'0') as i128))
            .ok_or_else(|| format!("decimal too large {text}"))?;
    }
    let mut frac_num = 0i128;
    for c in frac.bytes() {
        frac_num = frac_num
            .checked_mul(10)
            .and_then(|v| v.checked_add((c - b'0') as i128))
            .ok_or_else(|| format!("decimal too large {text}"))?;
        den = den.checked_mul(10).ok_or_else(|| format!("decimal too large {text}"))?;
    }
    let mut num = whole_num
        .checked_mul(den)
        .and_then(|v| v.checked_add(frac_num))
        .ok_or_else(|| format!("decimal too large {text}"))?;
    if neg {
        num = num.checked_neg().ok_or_else(|| format!("decimal too large {text}"))?;
    }
    Ok(RawDecimal { num, den })
}

fn raw_le(a: RawDecimal, b: RawDecimal) -> Result<bool, String> {
    let left = a.num.checked_mul(b.den).ok_or_else(|| "fixed-point overflow".to_string())?;
    let right = b.num.checked_mul(a.den).ok_or_else(|| "fixed-point overflow".to_string())?;
    Ok(left <= right)
}

fn raw_floor_ticks(raw: RawDecimal, scale: u32) -> Result<i128, String> {
    let scaled = raw
        .num
        .checked_mul(pow10(scale))
        .ok_or_else(|| "fixed-point overflow".to_string())?;
    Ok(div_floor(scaled, raw.den))
}

fn raw_ceil_ticks(raw: RawDecimal, scale: u32) -> Result<i128, String> {
    let scaled = raw
        .num
        .checked_mul(pow10(scale))
        .ok_or_else(|| "fixed-point overflow".to_string())?;
    Ok(div_ceil(scaled, raw.den))
}

fn div_floor(n: i128, d: i128) -> i128 {
    let q = n / d;
    let r = n % d;
    if r != 0 && ((r > 0) != (d > 0)) {
        q - 1
    } else {
        q
    }
}

fn div_ceil(n: i128, d: i128) -> i128 {
    let q = n / d;
    let r = n % d;
    if r != 0 && ((r > 0) == (d > 0)) {
        q + 1
    } else {
        q
    }
}

pub fn add(a: Interval, b: Interval) -> Result<Interval, String> {
    Ok(Interval {
        lo: a.lo.checked_add(b.lo).ok_or_else(|| "fixed-point overflow".to_string())?,
        hi: a.hi.checked_add(b.hi).ok_or_else(|| "fixed-point overflow".to_string())?,
    })
}

pub fn double(a: Interval) -> Result<Interval, String> {
    Ok(Interval {
        lo: a.lo.checked_mul(2).ok_or_else(|| "fixed-point overflow".to_string())?,
        hi: a.hi.checked_mul(2).ok_or_else(|| "fixed-point overflow".to_string())?,
    })
}

pub fn mul(a: Interval, b: Interval, scale: u32) -> Result<Interval, String> {
    let q = pow10(scale);
    let products = [
        a.lo.checked_mul(b.lo),
        a.lo.checked_mul(b.hi),
        a.hi.checked_mul(b.lo),
        a.hi.checked_mul(b.hi),
    ];
    let mut min_product: Option<i128> = None;
    let mut max_product: Option<i128> = None;
    for product in products {
        let value = product.ok_or_else(|| "fixed-point overflow".to_string())?;
        min_product = Some(min_product.map_or(value, |current| current.min(value)));
        max_product = Some(max_product.map_or(value, |current| current.max(value)));
    }
    Ok(Interval {
        lo: div_floor(min_product.unwrap(), q),
        hi: div_ceil(max_product.unwrap(), q),
    })
}

pub fn format_ticks(ticks: i128, scale: u32) -> String {
    if ticks == 0 {
        if scale == 0 {
            return "0".to_string();
        }
        return format!("0.{:0width$}", 0, width = scale as usize);
    }
    let q = pow10(scale);
    let sign = if ticks < 0 { "-" } else { "" };
    let n = ticks.abs();
    let whole = n / q;
    let frac = n % q;
    if scale == 0 {
        format!("{sign}{whole}")
    } else {
        format!("{sign}{whole}.{frac:0width$}", width = scale as usize)
    }
}
RS

cat > src/parser.rs <<'RS'
use crate::decimal::{self, Interval};
use std::collections::{BTreeMap, BTreeSet};

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
    pub positions: Vec<Position>,
    pub covariances: BTreeMap<(String, String), Interval>,
}

pub fn parse_portfolio(text: &str) -> Result<Portfolio, String> {
    let mut name: Option<String> = None;
    let mut scale: Option<u32> = None;
    let mut factors = BTreeMap::new();
    let mut positions = Vec::new();
    let mut position_ids = BTreeSet::new();
    let mut covariances = BTreeMap::new();

    for (idx, raw) in text.lines().enumerate() {
        let line_no = idx + 1;
        let line = raw.split('#').next().unwrap_or("").trim();
        if line.is_empty() {
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
                if name.is_none() {
                    return Err(format!("line {line_no}: missing portfolio"));
                }
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
                if !position_ids.insert((*id).to_string()) {
                    return Err(format!("line {line_no}: duplicate position"));
                }
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
                let key = if a < b {
                    ((*a).to_string(), (*b).to_string())
                } else {
                    ((*b).to_string(), (*a).to_string())
                };
                covariances.insert(key, decimal::parse_interval(value, sc)?);
            }
            _ => return Err(format!("line {line_no}: malformed record")),
        }
    }

    Ok(Portfolio {
        name: name.ok_or_else(|| "missing portfolio".to_string())?,
        scale: scale.ok_or_else(|| "missing scale".to_string())?,
        factors,
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
RS

cat > src/engine.rs <<'RS'
use crate::decimal::{self, Interval};
use crate::parser::Portfolio;
use std::collections::BTreeMap;

#[derive(Clone, Debug)]
pub struct BucketReport {
    pub bucket: String,
    pub exposure: Interval,
    pub variance: Interval,
}

#[derive(Clone, Debug)]
pub struct Report {
    pub portfolio: String,
    pub scale: u32,
    pub buckets: Vec<BucketReport>,
    pub total_exposure: Interval,
    pub total_variance: Interval,
}

pub fn evaluate(portfolio: &Portfolio) -> Result<Report, String> {
    let mut factor_exposure: BTreeMap<String, Interval> = BTreeMap::new();
    for name in portfolio.factors.keys() {
        factor_exposure.insert(name.clone(), decimal::zero());
    }

    for pos in &portfolio.positions {
        let term = decimal::mul(pos.amount, pos.sensitivity, portfolio.scale)?;
        let current = factor_exposure.get(&pos.factor).copied().unwrap_or_else(decimal::zero);
        factor_exposure.insert(pos.factor.clone(), decimal::add(current, term)?);
    }

    let mut grouped: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (name, factor) in &portfolio.factors {
        grouped.entry(factor.bucket.clone()).or_default().push(name.clone());
    }

    let mut buckets = Vec::new();
    let mut total_exposure = decimal::zero();
    let mut total_variance = decimal::zero();

    for (bucket, factor_names) in grouped {
        let mut exposure = decimal::zero();
        for name in &factor_names {
            exposure = decimal::add(exposure, factor_exposure[name])?;
        }

        let mut variance = decimal::zero();
        for i in 0..factor_names.len() {
            for j in i..factor_names.len() {
                let a = &factor_names[i];
                let b = &factor_names[j];
                let coeff = if i == j {
                    portfolio.factors[a].variance
                } else {
                    let key = if a < b { (a.clone(), b.clone()) } else { (b.clone(), a.clone()) };
                    portfolio.covariances.get(&key).copied().unwrap_or_else(decimal::zero)
                };
                let mut term = decimal::mul(factor_exposure[a], factor_exposure[b], portfolio.scale)?;
                term = decimal::mul(term, coeff, portfolio.scale)?;
                if i != j {
                    term = decimal::double(term)?;
                }
                variance = decimal::add(variance, term)?;
            }
        }

        total_exposure = decimal::add(total_exposure, exposure)?;
        total_variance = decimal::add(total_variance, variance)?;
        buckets.push(BucketReport { bucket, exposure, variance });
    }

    Ok(Report {
        portfolio: portfolio.name.clone(),
        scale: portfolio.scale,
        buckets,
        total_exposure,
        total_variance,
    })
}
RS

cargo build --quiet --locked
RSCRIPT_DONE=1
test "$RSCRIPT_DONE" = 1
