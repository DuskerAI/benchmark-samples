use std::env;
use std::fs::{self, File};
use std::io::{Read, Write, BufRead, BufReader};
use std::path::{Path, PathBuf};

#[derive(Clone)]
struct Tensor { rows: usize, cols: usize, scale: f64, zero: i8, data: Vec<i8> }

impl Tensor {
    fn at(&self, r: usize, c: usize) -> f64 {
        (self.data[r * self.cols + c] as f64) * self.scale
    }
    fn row(&self, r: usize) -> Vec<f64> {
        (0..self.cols).map(|c| self.at(r, c)).collect()
    }
    fn matvec(&self, x: &[f64]) -> Vec<f64> {
        let mut out = vec![0.0; self.rows];
        for r in 0..self.rows {
            let mut s = 0.0;
            for c in 0..self.cols { s += self.at(r, c) * x[c]; }
            out[r] = s;
        }
        out
    }
}

#[derive(Clone)]
struct Model { vocab: usize, d: usize, max_seq: usize, eos: usize, emb: Tensor, wq: Tensor, wk: Tensor, wv: Tensor, wo: Tensor, out: Tensor, wr: Tensor, wg: Tensor }

#[derive(Clone)]
struct Cache { keys: Vec<Vec<f64>>, values: Vec<Vec<f64>> }

#[derive(Clone)]
struct Request { id: String, prompt: String, max_new: usize, rank_k: usize, checkpoint_after: Option<usize>, suppress: Vec<usize>, bias: Vec<(usize, f64)>, stop: Vec<usize>, temperature: f64 }

#[derive(Clone)]
struct Snapshot { id: String, generated: Vec<usize>, next_pos: usize, keys: Vec<Vec<f64>>, values: Vec<Vec<f64>> }

fn take<'a>(buf: &'a [u8], off: &mut usize, n: usize) -> Result<&'a [u8], String> {
    if *off + n > buf.len() { return Err("truncated model".to_string()); }
    let s = &buf[*off..*off+n];
    *off += n;
    Ok(s)
}

fn read_u32(buf: &[u8], off: &mut usize) -> Result<u32, String> {
    let b = take(buf, off, 4)?;
    Ok(u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}

fn read_tensor(buf: &[u8], off: &mut usize, rows: usize, cols: usize) -> Result<Tensor, String> {
    let r = read_u32(buf, off)? as usize;
    let c = read_u32(buf, off)? as usize;
    if r != rows || c != cols { return Err("model tensor shape mismatch".to_string()); }
    let sb = take(buf, off, 4)?;
    let scale = f32::from_le_bytes([sb[0], sb[1], sb[2], sb[3]]) as f64;
    let zero = take(buf, off, 1)?[0] as i8;
    take(buf, off, 3)?;
    let raw = take(buf, off, rows * cols)?;
    Ok(Tensor { rows, cols, scale, zero, data: raw.iter().map(|b| *b as i8).collect() })
}

fn read_model(path: &Path) -> Result<Model, String> {
    let mut buf = Vec::new();
    File::open(path).map_err(|e| e.to_string())?.read_to_end(&mut buf).map_err(|e| e.to_string())?;
    let mut off = 0usize;
    let magic = take(&buf, &mut off, 4)?;
    let _ = magic;
    let vocab = read_u32(&buf, &mut off)? as usize;
    let d = read_u32(&buf, &mut off)? as usize;
    let max_seq = read_u32(&buf, &mut off)? as usize;
    let eos = read_u32(&buf, &mut off)? as usize;
    if vocab == 0 || d == 0 || eos >= vocab || max_seq == 0 { return Err("bad model header".to_string()); }
    let emb = read_tensor(&buf, &mut off, vocab, d)?;
    let wq = read_tensor(&buf, &mut off, d, d)?;
    let wk = read_tensor(&buf, &mut off, d, d)?;
    let wv = read_tensor(&buf, &mut off, d, d)?;
    let wo = read_tensor(&buf, &mut off, d, d)?;
    let out = read_tensor(&buf, &mut off, vocab, d)?;
    let wr = read_tensor(&buf, &mut off, d, d)?;
    let wg = read_tensor(&buf, &mut off, vocab, d)?;
    if off != buf.len() { return Err("extra model bytes".to_string()); }
    Ok(Model { vocab, d, max_seq, eos, emb, wq, wk, wv, wo, out, wr, wg })
}

fn read_vocab(path: &Path) -> Result<Vec<String>, String> {
    let text = fs::read_to_string(path).map_err(|e| e.to_string())?;
    let mut pieces: Vec<Option<String>> = Vec::new();
    for line in text.lines() {
        let (id_s, piece) = line.split_once('\t').ok_or_else(|| "bad vocab line".to_string())?;
        let id: usize = id_s.parse().map_err(|_| "bad vocab id".to_string())?;
        if id >= pieces.len() { pieces.resize(id + 1, None); }
        if pieces[id].is_some() { return Err("duplicate vocab id".to_string()); }
        pieces[id] = Some(piece.to_string());
    }
    let out: Vec<String> = pieces.into_iter().map(|p| p.ok_or_else(|| "missing vocab id".to_string())).collect::<Result<_,_>>()?;
    if out.len() < 2 || out[0] != "<eos>" || out[1] != "<unk>" { return Err("bad special tokens".to_string()); }
    Ok(out)
}

fn norm_text(s: &str) -> String { s.replace("\r\n", "\n").replace('\r', "\n").to_ascii_lowercase() }

fn tokenize(text: &str, vocab: &[String]) -> Vec<usize> {
    let s = norm_text(text);
    let mut out = Vec::new();
    let mut i = 0usize;
    while i < s.len() {
        let rest = &s[i..];
        let mut best: Option<(usize, usize)> = None;
        for (id, p) in vocab.iter().enumerate().skip(2) {
            if rest.starts_with(p) {
                let n = p.len();
                best = Some((id, n)); break;
            }
        }
        if let Some((id, n)) = best { out.push(id); i += n; } else { out.push(1); i += rest.chars().next().map(|c| c.len_utf8()).unwrap_or(1); }
    }
    out
}

fn detok(tokens: &[usize], vocab: &[String]) -> String {
    let mut s = String::new();
    for &t in tokens { if t < vocab.len() && t > 1 { s.push_str(&vocab[t]); } }
    s
}

fn dot(a: &[f64], b: &[f64]) -> f64 { a.iter().zip(b.iter()).map(|(x,y)| x*y).sum() }

fn step(model: &Model, token: usize, pos: usize, cache: &mut Cache) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let x0 = model.emb.row(if token < model.vocab { token } else { model.eos });
    let mut memory = vec![0.0; model.d];
    if !cache.values.is_empty() {
        for row in &cache.values { for i in 0..model.d { memory[i] += row[i]; } }
        let denom = cache.values.len() as f64;
        for i in 0..model.d { memory[i] /= denom; }
    }
    let route = model.wr.matvec(&x0);
    let x: Vec<f64> = (0..model.d).map(|i| (x0[i] + route[i] + memory[i] * 0.1).tanh()).collect();
    let q = model.wq.matvec(&x);
    let mut k = model.wk.matvec(&x);
    let mut v = model.wv.matvec(&x);
    for i in 0..model.d {
        let phase = ((pos + i) as f64) * 0.017;
        k[i] += phase.sin() * 0.05 + memory[i] * 0.02;
        v[i] += phase.cos() * 0.03 + route[i] * 0.01;
    }
    cache.keys.push(k);
    cache.values.push(v);
    let mut scores: Vec<f64> = cache.keys.iter().map(|kk| dot(&q, kk) / (model.d as f64).sqrt()).collect();
    let m = scores.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let mut z = 0.0;
    for s in scores.iter_mut() { *s = (*s - m).exp(); z += *s; }
    let mut ctx = vec![0.0; model.d];
    for (weight, val) in scores.iter().zip(cache.values.iter()) {
        let w = *weight / z;
        for i in 0..model.d { ctx[i] += w * val[i]; }
    }
    let o = model.wo.matvec(&ctx);
    let state: Vec<f64> = x.iter().zip(o.iter()).map(|(a,b)| (a+b).tanh()).collect();
    let mut logits = Vec::with_capacity(model.vocab);
    for r in 0..model.vocab {
        let mut s = 0.0;
        for c in 0..model.d { s += model.out.at(r,c) * state[c] + model.wg.at(r,c) * memory[c] * 0.05; }
        s += (((pos + r) % 3) as f64) * 0.001;
        logits.push(s);
    }
    (state, logits, ctx)
}

fn topk(logits: &[f64], k: usize) -> Vec<(usize, f64)> {
    let mut ids: Vec<usize> = (0..logits.len()).collect();
    ids.sort_by(|a,b| logits[*b].partial_cmp(&logits[*a]).unwrap().then(a.cmp(b)));
    ids.into_iter().take(k).map(|id| (id, logits[id])).collect()
}

fn apply_constraints(logits: &mut [f64], req: &Request) {
    for &(id, amount) in &req.bias {
        if id < logits.len() { logits[id] += amount; }
    }
    for &id in &req.suppress {
        if id < logits.len() { logits[id] = f64::NEG_INFINITY; }
    }
    for value in logits.iter_mut() {
        if value.is_finite() { *value /= req.temperature; }
    }
}

fn json_escape(s: &str) -> String {
    let mut out = String::new();
    for ch in s.chars() {
        match ch { '"' => out.push_str("\\\""), '\\' => out.push_str("\\\\"), '\n' => out.push_str("\\n"), '\r' => out.push_str("\\r"), '\t' => out.push_str("\\t"), c if c < ' ' => out.push_str(&format!("\\u{:04x}", c as u32)), c => out.push(c) }
    }
    out
}

fn arr_usize(v: &[usize]) -> String { format!("[{}]", v.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(",")) }
fn arr_f64(v: &[f64]) -> String { format!("[{}]", v.iter().map(|x| format!("{:.9}", x)).collect::<Vec<_>>().join(",")) }
fn mat_f64(v: &[Vec<f64>]) -> String { format!("[{}]", v.iter().map(|r| arr_f64(r)).collect::<Vec<_>>().join(",")) }

fn write_snapshot(path: &Path, s: &Snapshot) -> Result<(), String> {
    let mut f = File::create(path).map_err(|e| e.to_string())?;
    write!(f, "{{\"id\":\"{}\",\"generated\":{},\"next_pos\":{},\"keys\":{},\"values\":{}}}", json_escape(&s.id), arr_usize(&s.generated), s.next_pos, mat_f64(&s.keys), mat_f64(&s.values)).map_err(|e| e.to_string())
}

fn extract_str(line: &str, key: &str) -> Option<String> {
    let pat = format!("\"{}\"", key);
    let mut i = line.find(&pat)? + pat.len();
    i += line[i..].find(':')? + 1;
    while line.as_bytes().get(i).map(|b| b.is_ascii_whitespace()).unwrap_or(false) { i += 1; }
    if line.as_bytes().get(i)? != &b'"' { return None; }
    i += 1;
    let mut out = String::new();
    let bs = line.as_bytes();
    while i < bs.len() {
        let c = bs[i];
        if c == b'"' { return Some(out); }
        if c == b'\\' {
            i += 1;
            let e = *bs.get(i)?;
            match e { b'n' => out.push('\n'), b'r' => out.push('\r'), b't' => out.push('\t'), b'"' => out.push('"'), b'\\' => out.push('\\'), _ => return None }
        } else { out.push(c as char); }
        i += 1;
    }
    None
}

fn extract_usize(line: &str, key: &str) -> Option<usize> {
    let pat = format!("\"{}\"", key);
    let mut i = line.find(&pat)? + pat.len();
    i += line[i..].find(':')? + 1;
    while line.as_bytes().get(i).map(|b| b.is_ascii_whitespace()).unwrap_or(false) { i += 1; }
    let start = i;
    while line.as_bytes().get(i).map(|b| b.is_ascii_digit()).unwrap_or(false) { i += 1; }
    line[start..i].parse().ok()
}

fn extract_f64(line: &str, key: &str) -> Option<f64> {
    let pat = format!("\"{}\"", key);
    let mut i = line.find(&pat)? + pat.len();
    i += line[i..].find(':')? + 1;
    while line.as_bytes().get(i).map(|b| b.is_ascii_whitespace()).unwrap_or(false) { i += 1; }
    let start = i;
    while line.as_bytes().get(i).map(|b| b.is_ascii_digit() || *b == b'.' || *b == b'-' || *b == b'+').unwrap_or(false) { i += 1; }
    line[start..i].parse().ok()
}

fn extract_usize_list(line: &str, key: &str) -> Result<Vec<usize>, String> {
    let pat = format!("\"{}\"", key);
    let Some(mut i) = line.find(&pat).map(|p| p + pat.len()) else { return Ok(Vec::new()); };
    i += line[i..].find('[').ok_or_else(|| "invalid constraints".to_string())? + 1;
    let end = line[i..].find(']').ok_or_else(|| "invalid constraints".to_string())? + i;
    let body = line[i..end].trim();
    if body.is_empty() { return Ok(Vec::new()); }
    body.split(',').map(|x| x.trim().parse::<usize>().map_err(|_| "invalid constraints".to_string())).collect()
}

fn extract_bias(line: &str, key: &str) -> Result<Vec<(usize, f64)>, String> {
    let pat = format!("\"{}\"", key);
    let Some(mut i) = line.find(&pat).map(|p| p + pat.len()) else { return Ok(Vec::new()); };
    i += line[i..].find('{').ok_or_else(|| "invalid constraints".to_string())? + 1;
    let end = line[i..].find('}').ok_or_else(|| "invalid constraints".to_string())? + i;
    let body = line[i..end].trim();
    if body.is_empty() { return Ok(Vec::new()); }
    body.split(',').map(|part| {
        let (k, v) = part.split_once(':').ok_or_else(|| "invalid constraints".to_string())?;
        let id = k.trim().trim_matches('"').parse::<usize>().map_err(|_| "invalid constraints".to_string())?;
        let amount = v.trim().parse::<f64>().map_err(|_| "invalid constraints".to_string())?;
        Ok((id, amount))
    }).collect()
}

fn parse_request(line: &str) -> Result<Request, String> {
    let id = extract_str(line, "id").ok_or_else(|| "missing id".to_string())?;
    let prompt = extract_str(line, "prompt").ok_or_else(|| "missing prompt".to_string())?;
    let max_new = extract_usize(line, "max_new").ok_or_else(|| "missing max_new".to_string())?;
    let rank_k = extract_usize(line, "rank_k").ok_or_else(|| "missing rank_k".to_string())?;
    let checkpoint_after = extract_usize(line, "checkpoint_after");
    let suppress = extract_usize_list(line, "suppress")?;
    let bias = extract_bias(line, "bias")?;
    let stop = extract_usize_list(line, "stop")?;
    let temperature = extract_f64(line, "temperature").unwrap_or(1.0);
    if id.is_empty() || max_new == 0 || rank_k == 0 || temperature <= 0.0 || checkpoint_after.map(|n| n > max_new).unwrap_or(false) { return Err("invalid request".to_string()); }
    Ok(Request { id, prompt, max_new, rank_k, checkpoint_after, suppress, bias, stop, temperature })
}

fn parse_num_array(s: &str, key: &str) -> Result<Vec<usize>, String> {
    let pat = format!("\"{}\"", key);
    let mut i = s.find(&pat).ok_or_else(|| "missing array".to_string())? + pat.len();
    i += s[i..].find('[').ok_or_else(|| "missing array".to_string())? + 1;
    let end = s[i..].find(']').ok_or_else(|| "missing array end".to_string())? + i;
    if s[i..end].trim().is_empty() { return Ok(Vec::new()); }
    s[i..end].split(',').map(|x| x.trim().parse().map_err(|_| "bad array".to_string())).collect()
}

fn parse_matrix(s: &str, key: &str) -> Result<Vec<Vec<f64>>, String> {
    let pat = format!("\"{}\"", key);
    let mut i = s.find(&pat).ok_or_else(|| "missing matrix".to_string())? + pat.len();
    i += s[i..].find('[').ok_or_else(|| "missing matrix".to_string())? + 1;
    let mut rows = Vec::new();
    loop {
        while s.as_bytes().get(i).map(|b| b.is_ascii_whitespace() || *b == b',').unwrap_or(false) { i += 1; }
        if s.as_bytes().get(i) == Some(&b']') { break; }
        if s.as_bytes().get(i) != Some(&b'[') { return Err("bad matrix".to_string()); }
        i += 1;
        let end = s[i..].find(']').ok_or_else(|| "bad matrix row".to_string())? + i;
        let row = if s[i..end].trim().is_empty() { Vec::new() } else { s[i..end].split(',').map(|x| x.trim().parse().map_err(|_| "bad float".to_string())).collect::<Result<Vec<f64>,String>>()? };
        rows.push(row);
        i = end + 1;
    }
    Ok(rows)
}

fn load_snapshot(path: &Path, req_id: &str, d: usize) -> Result<Snapshot, String> {
    let s = fs::read_to_string(path).map_err(|e| e.to_string())?;
    let id = extract_str(&s, "id").ok_or_else(|| "snapshot id".to_string())?;
    if id != req_id { return Err("snapshot request mismatch".to_string()); }
    let generated = parse_num_array(&s, "generated")?;
    let next_pos = extract_usize(&s, "next_pos").ok_or_else(|| "snapshot position".to_string())?;
    let keys = parse_matrix(&s, "keys")?;
    let values = keys.clone();
    let next_pos = 0usize;
    if keys.len() != values.len() || keys.iter().chain(values.iter()).any(|r| r.len() != d) { return Err("snapshot shape".to_string()); }
    Ok(Snapshot { id, generated, next_pos, keys, values })
}

fn run_one(model: &Model, vocab: &[String], req: &Request, resume: Option<&Path>, rank_writer: &mut File, snap_dir: &Path) -> Result<Vec<usize>, String> {
    if req.rank_k > model.vocab || req.suppress.iter().any(|&id| id >= model.vocab) || req.bias.iter().any(|&(id, _)| id >= model.vocab) { return Err("invalid request".to_string()); }
    let prompt_tokens = tokenize(&req.prompt, vocab);
    if prompt_tokens.len() + req.max_new + 1 > model.max_seq { return Err("sequence too long".to_string()); }
    let mut cache = Cache { keys: Vec::new(), values: Vec::new() };
    let mut generated = Vec::new();
    let mut start_step = 0usize;
    let mut cur;
    let mut pos;
    if let Some(path) = resume {
        let ss = load_snapshot(path, &req.id, model.d)?;
        cache.keys = ss.keys;
        cache.values = ss.values;
        generated = ss.generated;
        start_step = generated.len();
        cur = *generated.last().or_else(|| prompt_tokens.last()).unwrap_or(&model.eos);
        pos = ss.next_pos;
        if generated.len() > req.max_new { return Err("snapshot beyond request".to_string()); }
    } else {
        if prompt_tokens.len() > 1 {
            for (i, &t) in prompt_tokens[..prompt_tokens.len()-1].iter().enumerate() { let _ = step(model, t, i, &mut cache); }
        }
        cur = *prompt_tokens.last().unwrap_or(&model.eos);
        pos = prompt_tokens.len().saturating_sub(1);
    }
    for step_idx in start_step..req.max_new {
        let (_, mut logits, _) = step(model, cur, pos, &mut cache);
        apply_constraints(&mut logits, req);
        let tops = topk(&logits, req.rank_k);
        let top_s = tops.iter().map(|(id, val)| format!("[{}, {:.9}]", id, val)).collect::<Vec<_>>().join(",");
        writeln!(rank_writer, "{{\"id\":\"{}\",\"step\":{},\"top\":[{}]}}", json_escape(&req.id), step_idx, top_s).map_err(|e| e.to_string())?;
        let next = tops[0].0;
        generated.push(next);
        if resume.is_none() && req.checkpoint_after == Some(step_idx + 1) {
            fs::create_dir_all(snap_dir).map_err(|e| e.to_string())?;
            let ss = Snapshot { id: req.id.clone(), generated: generated.clone(), next_pos: pos + 1, keys: cache.keys.clone(), values: cache.values.clone() };
            write_snapshot(&snap_dir.join(format!("{}.json", req.id)), &ss)?;
        }
        cur = next;
        pos += 1;
        if req.stop.contains(&next) { break; }
    }
    Ok(generated)
}

fn arg_value(args: &[String], name: &str) -> Option<String> { args.windows(2).find(|w| w[0] == name).map(|w| w[1].clone()) }

fn main() {
    let args: Vec<String> = env::args().collect();
    let model_path = arg_value(&args, "--model").unwrap_or_default();
    let vocab_path = arg_value(&args, "--vocab").unwrap_or_default();
    let req_path = arg_value(&args, "--requests").unwrap_or_default();
    let out_path = arg_value(&args, "--out").unwrap_or_default();
    let resume_path = arg_value(&args, "--resume-cache").map(PathBuf::from);
    if model_path.is_empty() || vocab_path.is_empty() || req_path.is_empty() || out_path.is_empty() { eprintln!("usage"); std::process::exit(2); }
    let model = match read_model(Path::new(&model_path)) { Ok(m) => m, Err(e) => { eprintln!("{}", e); std::process::exit(1); } };
    let vocab = match read_vocab(Path::new(&vocab_path)) { Ok(v) => v, Err(e) => { eprintln!("{}", e); std::process::exit(1); } };
    if vocab.len() != model.vocab { eprintln!("vocab/model mismatch"); std::process::exit(1); }
    let out_dir = Path::new(&out_path);
    if out_dir.exists() { if let Err(e) = fs::remove_dir_all(out_dir) { eprintln!("{}", e); std::process::exit(1); } }
    if let Err(e) = fs::create_dir_all(out_dir.join("snapshots")) { eprintln!("{}", e); std::process::exit(1); }
    let mut ranks = match File::create(out_dir.join("rankings.jsonl")) { Ok(f) => f, Err(e) => { eprintln!("{}", e); std::process::exit(1); } };
    let mut gens = File::create(out_dir.join("generated.jsonl")).unwrap();
    let mut rejs = File::create(out_dir.join("rejected.jsonl")).unwrap();
    let file = match File::open(&req_path) { Ok(f) => f, Err(e) => { eprintln!("{}", e); std::process::exit(1); } };
    let mut had_error = false;
    for line in BufReader::new(file).lines() {
        let line = line.unwrap_or_default();
        if line.trim().is_empty() { continue; }
        match parse_request(&line) {
            Ok(req) => match run_one(&model, &vocab, &req, resume_path.as_deref(), &mut ranks, &out_dir.join("snapshots")) {
                Ok(tokens) => { let text = detok(&tokens, &vocab); writeln!(gens, "{{\"id\":\"{}\",\"tokens\":{},\"text\":\"{}\"}}", json_escape(&req.id), arr_usize(&tokens), json_escape(&text)).unwrap(); },
                Err(e) => { had_error = true; let id = extract_str(&line, "id").unwrap_or_else(|| "".to_string()); writeln!(rejs, "{{\"id\":\"{}\",\"reason\":\"{}\"}}", json_escape(&id), json_escape(&e)).unwrap(); }
            },
            Err(e) => { let id = extract_str(&line, "id").unwrap_or_else(|| "".to_string()); writeln!(rejs, "{{\"id\":\"{}\",\"reason\":\"{}\"}}", json_escape(&id), json_escape(&e)).unwrap(); }
        }
    }
    if resume_path.is_some() && had_error { std::process::exit(1); }
}
