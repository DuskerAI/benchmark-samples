pub fn numbers(text: &str) -> Vec<f64> {
    let mut out = Vec::new();
    let mut buf = String::new();
    let mut in_num = false;
    for ch in text.chars() {
        let numeric = ch.is_ascii_digit() || matches!(ch, '-' | '+' | '.' | 'e' | 'E');
        if numeric { buf.push(ch); in_num = true; }
        else if in_num { if let Ok(v) = buf.parse::<f64>() { out.push(v); } buf.clear(); in_num = false; }
    }
    if in_num { if let Ok(v) = buf.parse::<f64>() { out.push(v); } }
    out
}
