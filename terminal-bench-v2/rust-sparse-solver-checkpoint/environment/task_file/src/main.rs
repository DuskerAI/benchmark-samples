use std::env;
use std::error::Error;
use std::fmt::Write as _;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

type DynResult<T> = Result<T, Box<dyn Error>>;

#[derive(Clone)]
struct Matrix {
    n: usize,
    a: Vec<Vec<f64>>,
}

struct Case {
    name: String,
    matrix: PathBuf,
    rhs: PathBuf,
    checkpoint: Option<PathBuf>,
}

struct Outcome {
    name: String,
    n: usize,
    x: Vec<f64>,
    residual: f64,
    certified: bool,
    used_checkpoint: bool,
}

fn main() {
    if let Err(e) = real_main() {
        eprintln!("spsolve: {e}");
        std::process::exit(1);
    }
}

fn real_main() -> DynResult<()> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 || args[1] != "run" {
        return Err("usage: spsolve run <job-file> <report-file>".into());
    }
    let job_path = PathBuf::from(&args[2]);
    let report_path = PathBuf::from(&args[3]);
    let cases = read_job(&job_path)?;
    let mut outcomes = Vec::new();
    for case in cases {
        outcomes.push(run_case(&case)?);
    }
    write_report(&report_path, &outcomes)?;
    Ok(())
}

fn read_job(path: &Path) -> DynResult<Vec<Case>> {
    let base = path.parent().unwrap_or_else(|| Path::new("."));
    let text = fs::read_to_string(path)?;
    let mut cases = Vec::new();
    let mut current_name: Option<String> = None;
    let mut matrix: Option<PathBuf> = None;
    let mut rhs: Option<PathBuf> = None;
    let mut checkpoint: Option<PathBuf> = None;
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut parts = line.split_whitespace();
        let key = parts.next().ok_or("empty job line")?;
        match key {
            "case" => {
                if current_name.is_some() {
                    return Err("nested case".into());
                }
                current_name = Some(parts.next().ok_or("case name missing")?.to_string());
                matrix = None;
                rhs = None;
                checkpoint = None;
            }
            "matrix" => matrix = Some(resolve(base, parts.next().ok_or("matrix path missing")?)),
            "rhs" => rhs = Some(resolve(base, parts.next().ok_or("rhs path missing")?)),
            "checkpoint" => checkpoint = Some(resolve(base, parts.next().ok_or("checkpoint path missing")?)),
            "end" => {
                let name = current_name.take().ok_or("end outside case")?;
                cases.push(Case {
                    name,
                    matrix: matrix.take().ok_or("matrix missing")?,
                    rhs: rhs.take().ok_or("rhs missing")?,
                    checkpoint: checkpoint.take(),
                });
            }
            _ => return Err(format!("unknown job key {key}").into()),
        }
    }
    if current_name.is_some() {
        return Err("unterminated case".into());
    }
    if cases.is_empty() {
        return Err("empty job".into());
    }
    Ok(cases)
}

fn resolve(base: &Path, raw: &str) -> PathBuf {
    let p = PathBuf::from(raw);
    if p.is_absolute() { p } else { base.join(p) }
}

fn run_case(case: &Case) -> DynResult<Outcome> {
    let matrix = read_matrix(&case.matrix)?;
    let rhs = read_rhs(&case.rhs)?;
    if rhs.len() != matrix.n {
        return Err("rhs length mismatch".into());
    }

    let pattern = pattern_fingerprint(&matrix);
    let numeric = numeric_fingerprint(&matrix);
    let mut used_checkpoint = false;
    let lu = if let Some(path) = &case.checkpoint {
        if let Some(saved) = load_checkpoint(path, matrix.n, pattern)? {
            used_checkpoint = true;
            saved
        } else {
            factorize(&matrix.a)?
        }
    } else {
        factorize(&matrix.a)?
    };
    let x = solve_lu(&lu, &rhs)?;
    if let Some(path) = &case.checkpoint {
        save_checkpoint(path, matrix.n, pattern, numeric, &lu)?;
    }
    let residual = residual_norm(&matrix, &rhs, &x);
    let certified = residual <= 1e-8 * (1.0 + norm_inf(&rhs));
    if !certified {
        return Err("residual certification failed".into());
    }
    Ok(Outcome { name: case.name.clone(), n: matrix.n, x, residual, certified, used_checkpoint })
}

fn read_matrix(path: &Path) -> DynResult<Matrix> {
    let text = fs::read_to_string(path)?;
    let mut lines = text.lines();
    let header = lines.next().ok_or("empty matrix")?.trim().to_ascii_lowercase();
    if !header.starts_with("%%matrixmarket matrix coordinate real") {
        return Err("unsupported matrix header".into());
    }
    let symmetric = header.contains("symmetric");
    let mut dims = None;
    for line in &mut lines {
        let t = line.trim();
        if t.is_empty() || t.starts_with('%') {
            continue;
        }
        let vals: Vec<&str> = t.split_whitespace().collect();
        if vals.len() != 3 {
            return Err("bad size line".into());
        }
        let n: usize = vals[0].parse()?;
        let m: usize = vals[1].parse()?;
        let nz: usize = vals[2].parse()?;
        if n != m { return Err("matrix must be square".into()); }
        dims = Some((n, nz));
        break;
    }
    let (n, nz) = dims.ok_or("missing matrix size")?;
    let mut a = vec![vec![0.0; n]; n];
    let mut seen = 0usize;
    for line in lines {
        let t = line.trim();
        if t.is_empty() || t.starts_with('%') { continue; }
        let vals: Vec<&str> = t.split_whitespace().collect();
        if vals.len() != 3 { return Err("bad entry line".into()); }
        let r: usize = vals[0].parse()?;
        let c: usize = vals[1].parse()?;
        let v: f64 = vals[2].parse()?;
        if r == 0 || c == 0 || r > n || c > n { return Err("index out of range".into()); }
        let i = r - 1;
        let j = c - 1;
        a[i][j] = v;
        if symmetric && i != j && i < j {
            a[j][i] = v;
        }
        seen += 1;
    }
    if seen != nz { return Err("entry count mismatch".into()); }
    Ok(Matrix { n, a })
}

fn read_rhs(path: &Path) -> DynResult<Vec<f64>> {
    let text = fs::read_to_string(path)?;
    let mut out = Vec::new();
    for line in text.lines() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') { continue; }
        out.push(t.parse()?);
    }
    Ok(out)
}

fn factorize(a: &[Vec<f64>]) -> DynResult<Vec<Vec<f64>>> {
    let n = a.len();
    let mut lu = a.to_vec();
    for k in 0..n {
        let pivot = lu[k][k];
        if pivot.abs() < 1e-18 {
            return Err("singular matrix".into());
        }
        for i in k + 1..n {
            lu[i][k] /= pivot;
            for j in k + 1..n {
                lu[i][j] -= lu[i][k] * lu[k][j];
            }
        }
    }
    Ok(lu)
}

fn solve_lu(lu: &[Vec<f64>], rhs: &[f64]) -> DynResult<Vec<f64>> {
    let n = lu.len();
    let mut y = rhs.to_vec();
    for i in 0..n {
        for j in 0..i {
            y[i] -= lu[i][j] * y[j];
        }
    }
    let mut x = y;
    for i in (0..n).rev() {
        for j in i + 1..n {
            x[i] -= lu[i][j] * x[j];
        }
        x[i] /= lu[i][i];
    }
    Ok(x)
}

fn residual_norm(matrix: &Matrix, rhs: &[f64], x: &[f64]) -> f64 {
    let mut worst = 0.0f64;
    for i in 0..matrix.n {
        let mut s = 0.0;
        for j in 0..matrix.n {
            s += matrix.a[i][j] * x[j];
        }
        worst = worst.max((s - rhs[i]).abs());
    }
    worst
}

fn norm_inf(v: &[f64]) -> f64 {
    v.iter().fold(0.0, |m, x| m.max(x.abs()))
}

fn pattern_fingerprint(matrix: &Matrix) -> u64 {
    let mut h = 1469598103934665603u64;
    for i in 0..matrix.n {
        for j in 0..matrix.n {
            if matrix.a[i][j] != 0.0 {
                h ^= ((i as u64) << 32) ^ j as u64;
                h = h.wrapping_mul(1099511628211);
            }
        }
    }
    h
}

fn numeric_fingerprint(matrix: &Matrix) -> u64 {
    let mut h = pattern_fingerprint(matrix);
    for row in &matrix.a {
        for v in row {
            h ^= v.to_bits();
            h = h.wrapping_mul(1099511628211);
        }
    }
    h
}

fn load_checkpoint(path: &Path, n: usize, pattern: u64) -> DynResult<Option<Vec<Vec<f64>>>> {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    let mut lines = text.lines();
    if lines.next() != Some("SPCHK1") { return Ok(None); }
    let n_line = lines.next().ok_or("bad checkpoint")?;
    let p_line = lines.next().ok_or("bad checkpoint")?;
    let saved_n: usize = n_line.strip_prefix("n ").ok_or("bad checkpoint")?.parse()?;
    let saved_pattern: u64 = p_line.strip_prefix("pattern ").ok_or("bad checkpoint")?.parse()?;
    let _ = lines.next();
    if saved_n != n || saved_pattern != pattern { return Ok(None); }
    if lines.next() != Some("factors") { return Ok(None); }
    let mut lu = Vec::new();
    for _ in 0..n {
        let row = lines.next().ok_or("bad checkpoint")?;
        let vals: Result<Vec<f64>, _> = row.split_whitespace().map(str::parse).collect();
        let vals = vals?;
        if vals.len() != n { return Ok(None); }
        lu.push(vals);
    }
    Ok(Some(lu))
}

fn save_checkpoint(path: &Path, n: usize, pattern: u64, numeric: u64, lu: &[Vec<f64>]) -> DynResult<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut text = String::new();
    writeln!(&mut text, "SPCHK1")?;
    writeln!(&mut text, "n {n}")?;
    writeln!(&mut text, "pattern {pattern}")?;
    writeln!(&mut text, "numeric {numeric}")?;
    writeln!(&mut text, "factors")?;
    for row in lu {
        for (idx, value) in row.iter().enumerate() {
            if idx > 0 { text.push(' '); }
            write!(&mut text, "{value:.17e}")?;
        }
        text.push('\n');
    }
    let mut file = fs::File::create(path)?;
    file.write_all(text.as_bytes())?;
    Ok(())
}

fn write_report(path: &Path, outcomes: &[Outcome]) -> DynResult<()> {
    if let Some(parent) = path.parent() { fs::create_dir_all(parent)?; }
    let mut text = String::from("{\"cases\":[");
    for (idx, o) in outcomes.iter().enumerate() {
        if idx > 0 { text.push(','); }
        write!(&mut text, "{{\"name\":\"{}\",\"n\":{},\"solution\":[", escape_json(&o.name), o.n)?;
        for (j, value) in o.x.iter().enumerate() {
            if j > 0 { text.push(','); }
            write!(&mut text, "{value:.17e}")?;
        }
        write!(&mut text, "],\"residual_norm\":{:.17e},\"certified\":{},\"used_checkpoint\":{}}}", o.residual, o.certified, o.used_checkpoint)?;
    }
    text.push_str("]}\n");
    fs::write(path, text)?;
    Ok(())
}

fn escape_json(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}
