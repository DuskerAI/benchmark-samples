#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Interval {
    pub lo: i128,
    pub hi: i128,
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
    let v = parse_to_ticks(text, scale)?;
    Ok(Interval { lo: v, hi: v })
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
    let lo = parse_to_ticks(a.trim(), scale)?;
    let hi = parse_to_ticks(b.trim(), scale)?;
    if lo > hi {
        return Err(format!("invalid interval {text}"));
    }
    Ok(Interval { lo, hi })
}

fn parse_to_ticks(text: &str, scale: u32) -> Result<i128, String> {
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
    let q = pow10(scale);
    let mut int_part = 0i128;
    for c in whole.bytes() {
        int_part = int_part
            .checked_mul(10)
            .and_then(|v| v.checked_add((c - b'0') as i128))
            .ok_or_else(|| format!("decimal too large {text}"))?;
    }
    let mut frac_part = 0i128;
    let mut den = 1i128;
    for c in frac.bytes() {
        frac_part = frac_part
            .checked_mul(10)
            .and_then(|v| v.checked_add((c - b'0') as i128))
            .ok_or_else(|| format!("decimal too large {text}"))?;
        den = den.checked_mul(10).ok_or_else(|| format!("decimal too large {text}"))?;
    }
    let scaled = int_part
        .checked_mul(q)
        .and_then(|v| v.checked_add(frac_part.checked_mul(q)? / den))
        .ok_or_else(|| format!("decimal too large {text}"))?;
    Ok(if neg { -scaled } else { scaled })
}

fn div_floor(n: i128, d: i128) -> i128 {
    if n >= 0 {
        n / d
    } else {
        -((-n + d - 1) / d)
    }
}

fn div_ceil(n: i128, d: i128) -> i128 {
    if n >= 0 {
        (n + d - 1) / d
    } else {
        -((-n) / d)
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
    let lo_raw = a.lo.checked_mul(b.lo).ok_or_else(|| "fixed-point overflow".to_string())?;
    let hi_raw = a.hi.checked_mul(b.hi).ok_or_else(|| "fixed-point overflow".to_string())?;
    Ok(Interval {
        lo: div_floor(lo_raw.min(hi_raw), q),
        hi: div_ceil(lo_raw.max(hi_raw), q),
    })
}

pub fn format_ticks(ticks: i128, scale: u32) -> String {
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
