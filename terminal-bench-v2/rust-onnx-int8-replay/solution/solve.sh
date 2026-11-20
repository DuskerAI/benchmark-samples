#!/bin/bash
set -euo pipefail

export PATH="/usr/local/cargo/bin:$PATH"

cat > /app/src/main.rs <<'RS'
use std::env;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

#[derive(Clone)]
struct Model {
    classes: usize,
    input_len: usize,
    input_zp: i32,
    conv_out: usize,
    kernel: usize,
    pad_left: isize,
    pad_right: isize,
    conv_zp: Vec<i32>,
    conv_bias: Vec<i32>,
    conv_w: Vec<Vec<i32>>,
    affine_scales: Vec<f64>,
    affine_zp: Vec<i32>,
    affine_bias: Vec<i64>,
    affine_w: Vec<Vec<i32>>,
    temperature: f64,
}

#[derive(Clone)]
struct Request {
    seq: u64,
    id: String,
    input: Vec<i32>,
    mask: Vec<i32>,
}

struct Args {
    case_dir: PathBuf,
    out_dir: PathBuf,
    checkpoint: Option<PathBuf>,
    resume: Option<PathBuf>,
    limit: Option<usize>,
}

fn usage() -> ! {
    eprintln!("usage: qcert --case CASE_DIR --out OUT_DIR [--checkpoint FILE] [--resume FILE] [--limit N]");
    std::process::exit(2);
}

fn parse_args() -> Args {
    let mut case_dir = None;
    let mut out_dir = None;
    let mut checkpoint = None;
    let mut resume = None;
    let mut limit = None;
    let mut it = env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--case" => case_dir = it.next().map(PathBuf::from),
            "--out" => out_dir = it.next().map(PathBuf::from),
            "--checkpoint" => checkpoint = it.next().map(PathBuf::from),
            "--resume" => resume = it.next().map(PathBuf::from),
            "--limit" => limit = it.next().and_then(|s| s.parse::<usize>().ok()),
            _ => usage(),
        }
    }
    Args {
        case_dir: case_dir.unwrap_or_else(|| usage()),
        out_dir: out_dir.unwrap_or_else(|| usage()),
        checkpoint,
        resume,
        limit,
    }
}

fn parse_i32s(s: &str) -> Vec<i32> {
    s.split_whitespace().map(|x| x.parse::<i32>().unwrap()).collect()
}

fn parse_f64s(s: &str) -> Vec<f64> {
    s.split_whitespace().map(|x| x.parse::<f64>().unwrap()).collect()
}

fn read_model(path: &Path) -> Model {
    let text = fs::read_to_string(path).unwrap();
    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    let mut i = 0usize;
    let mut classes = 0usize;
    let mut input_len = 0usize;
    let mut input_zp = 0i32;
    let mut conv_out = 0usize;
    let mut kernel = 0usize;
    let mut pad_left = 0isize;
    let mut pad_right = 0isize;
    let mut conv_zp = Vec::new();
    let mut conv_bias = Vec::new();
    let mut conv_w = Vec::new();
    let mut affine_scales = Vec::new();
    let mut affine_zp = Vec::new();
    let mut affine_bias = Vec::new();
    let mut affine_w = Vec::new();
    let mut temperature = 1.0f64;
    while i < lines.len() {
        let line = lines[i].trim();
        let mut parts = line.splitn(2, ' ');
        let key = parts.next().unwrap();
        let rest = parts.next().unwrap_or("").trim();
        match key {
            "classes" => classes = rest.parse().unwrap(),
            "input_len" => input_len = rest.parse().unwrap(),
            "input_scale" => {}
            "input_zp" => input_zp = rest.parse().unwrap(),
            "conv_out" => conv_out = rest.parse().unwrap(),
            "kernel" => kernel = rest.parse().unwrap(),
            "pad_left" => pad_left = rest.parse().unwrap(),
            "pad_right" => pad_right = rest.parse().unwrap(),
            "conv_scales" => {}
            "conv_zero_points" => conv_zp = parse_i32s(rest),
            "conv_bias" => conv_bias = parse_i32s(rest),
            "conv_weights" => {
                conv_w.clear();
                for _ in 0..conv_out {
                    i += 1;
                    conv_w.push(parse_i32s(lines[i]));
                }
            }
            "affine_scales" => affine_scales = parse_f64s(rest),
            "affine_zero_points" => affine_zp = parse_i32s(rest),
            "affine_bias" => affine_bias = parse_i32s(rest).into_iter().map(i64::from).collect(),
            "affine_weights" => {
                affine_w.clear();
                for _ in 0..classes {
                    i += 1;
                    affine_w.push(parse_i32s(lines[i]));
                }
            }
            "temperature" => temperature = rest.parse().unwrap(),
            _ => {}
        }
        i += 1;
    }
    Model { classes, input_len, input_zp, conv_out, kernel, pad_left, pad_right, conv_zp, conv_bias, conv_w, affine_scales, affine_zp, affine_bias, affine_w, temperature }
}

fn read_requests(path: &Path) -> Vec<Request> {
    let text = fs::read_to_string(path).unwrap();
    let mut out = Vec::new();
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let cols: Vec<&str> = line.split('\t').collect();
        if cols.len() != 4 { continue; }
        out.push(Request { seq: cols[0].parse().unwrap(), id: cols[1].to_string(), input: parse_i32s(cols[2]), mask: parse_i32s(cols[3]) });
    }
    out
}

fn softmax(vals: &[f64]) -> Vec<f64> {
    let top = vals.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let exps: Vec<f64> = vals.iter().map(|v| (v - top).exp()).collect();
    let sum: f64 = exps.iter().sum();
    exps.into_iter().map(|x| x / sum).collect()
}

fn infer(model: &Model, req: &Request) -> (Vec<f64>, usize, f64) {
    assert_eq!(req.input.len(), model.input_len);
    assert_eq!(req.mask.len(), model.input_len);
    let out_len = (model.input_len as isize + model.pad_left + model.pad_right - model.kernel as isize + 1) as usize;
    let mut features = vec![0i64; model.conv_out];
    for oc in 0..model.conv_out {
        let wzp = model.conv_zp[oc];
        for pos in 0..out_len {
            let mut acc = i64::from(model.conv_bias[oc]);
            for k in 0..model.kernel {
                let idx = pos as isize + k as isize - model.pad_left;
                let xq = if idx < 0 || idx >= model.input_len as isize || req.mask[idx as usize] == 0 { model.input_zp } else { req.input[idx as usize] };
                acc += i64::from(xq - model.input_zp) * i64::from(model.conv_w[oc][k] - wzp);
            }
            features[oc] += acc;
        }
    }
    let mut logits = Vec::new();
    for c in 0..model.classes {
        let mut z = model.affine_bias[c];
        for oc in 0..model.conv_out {
            z += features[oc] * i64::from(model.affine_w[c][oc] - model.affine_zp[c]);
        }
        logits.push(z as f64 * model.affine_scales[c] * model.temperature);
    }
    let probs = softmax(&logits);
    let mut best = 0usize;
    let mut second = 0f64;
    for (i, p) in probs.iter().enumerate() {
        if *p > probs[best] {
            second = probs[best];
            best = i;
        } else if i != best && *p > second {
            second = *p;
        }
    }
    let margin = probs[best] - second;
    (probs, best, margin)
}

fn fmt_probs(p: &[f64]) -> String {
    let mut s = String::from("[");
    for (i, v) in p.iter().enumerate() {
        if i > 0 { s.push(','); }
        s.push_str(&format!("{:.12}", v));
    }
    s.push(']');
    s
}

fn json_string(s: &str) -> String {
    let mut out = String::from("\"");
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c < ' ' => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn line_for(model: &Model, req: &Request) -> String {
    let (p, class_id, margin) = infer(model, req);
    format!("{{\"seq\":{},\"request_id\":{},\"class\":{},\"confidence\":{:.12},\"probabilities\":{},\"margin\":{:.12}}}",
        req.seq, json_string(&req.id), class_id, p[class_id], fmt_probs(&p), margin)
}

fn digest(lines: &[String]) -> u64 {
    let mut h = 1469598103934665603u64;
    for line in lines {
        for b in line.as_bytes().iter().chain(std::iter::once(&b'\n')) {
            h ^= u64::from(*b);
            h = h.wrapping_mul(1099511628211);
        }
    }
    h
}

fn read_checkpoint(path: &Path) -> (usize, Vec<String>) {
    let mut text = String::new();
    File::open(path).unwrap().read_to_string(&mut text).unwrap();
    let mut lines = text.lines();
    let next = lines.next().unwrap_or("0").parse().unwrap_or(0usize);
    (next, lines.map(|s| s.to_string()).collect())
}

fn write_outputs(out_dir: &Path, lines: &[String], classes: usize) {
    if out_dir.exists() { fs::remove_dir_all(out_dir).unwrap(); }
    fs::create_dir_all(out_dir).unwrap();
    let mut pred = File::create(out_dir.join("predictions.jsonl")).unwrap();
    for line in lines { writeln!(pred, "{}", line).unwrap(); }
    let mut counts = vec![0usize; classes];
    for line in lines {
        if let Some(i) = line.find("\"class\":") {
            let rest = &line[i + 8..];
            let n: usize = rest.split(|c| c == ',' || c == '}').next().unwrap().parse().unwrap();
            counts[n] += 1;
        }
    }
    let counts_s = counts.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(",");
    let mut cert = File::create(out_dir.join("cert.json")).unwrap();
    writeln!(cert, "{{\"records\":{},\"classes\":{},\"class_counts\":[{}],\"replay_digest\":\"{:016x}\"}}", lines.len(), classes, counts_s, digest(lines)).unwrap();
}

fn main() {
    let args = parse_args();
    let model = read_model(&args.case_dir.join("model.qnn"));
    let mut requests = read_requests(&args.case_dir.join("requests.tsv"));
    requests.sort_by_key(|r| r.seq);
    let (mut next, mut lines) = if let Some(ref p) = args.resume {
        read_checkpoint(p)
    } else { (0usize, Vec::new()) };
    let mut added = 0usize;
    while next < requests.len() {
        if args.limit.map(|n| added >= n).unwrap_or(false) { break; }
        lines.push(line_for(&model, &requests[next]));
        next += 1;
        added += 1;
    }
    if let Some(ref ck) = args.checkpoint {
        if let Some(parent) = ck.parent() { fs::create_dir_all(parent).unwrap(); }
        let mut f = File::create(ck).unwrap();
        writeln!(f, "{}", next).unwrap();
        for line in &lines { writeln!(f, "{}", line).unwrap(); }
    }
    write_outputs(&args.out_dir, &lines, model.classes);
}
RS

cd /app
cargo build --release
