#!/bin/bash
set -euo pipefail

cd /app

cat > src/main.rs <<'RS'
use std::collections::{BTreeMap, HashMap, HashSet};
use std::env;
use std::fs;
use std::io::{self, Read};
use std::path::Path;

#[derive(Clone, Debug)]
struct State {
    generation: u32,
    last_seq: u64,
    accounts: BTreeMap<String, i64>,
    findings: Vec<String>,
}

#[derive(Clone, Debug)]
struct Record {
    generation: u32,
    seq: u64,
    op: u8,
    key: String,
    value: i64,
}

#[derive(Clone, Debug)]
struct Anchor {
    seq: u64,
    key: String,
    value: i64,
}

#[derive(Clone, Debug)]
struct RangeKey {
    generation: u32,
    start_seq: u64,
    end_seq: u64,
    key: String,
}

#[derive(Clone, Debug)]
struct Limit {
    min: i64,
    max: i64,
}

#[derive(Clone, Debug)]
struct Rollup {
    generation: u32,
    start_seq: u64,
    end_seq: u64,
    key: String,
    expected_delta: i64,
}

fn main() {
    if let Err(err) = run() {
        eprintln!("{err}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 3 {
        return Err("usage: store-evidence-attest <state-dir> <report-json>".to_string());
    }

    let state_dir = Path::new(&args[1]);
    let out_path = Path::new(&args[2]);
    let mut state = read_snapshot(&state_dir.join("snapshot.bin")).map_err(|e| format!("snapshot: {e}"))?;
    let records = read_wal(&state_dir.join("wal.log")).map_err(|e| format!("wal: {e}"))?;
    let quarantines = read_ranges(&state_dir.join("quarantine.bin"), b"QRN1", "quarantine")
        .map_err(|e| format!("quarantine: {e}"))?;
    let leases = read_ranges(&state_dir.join("leases.bin"), b"LSE1", "lease")
        .map_err(|e| format!("leases: {e}"))?;
    let limits = read_limits(&state_dir.join("limits.bin")).map_err(|e| format!("limits: {e}"))?;
    let rollups = read_rollups(&state_dir.join("rollups.bin")).map_err(|e| format!("rollups: {e}"))?;
    let anchors = read_anchors(&state_dir.join("anchors.bin")).map_err(|e| format!("anchors: {e}"))?;

    let deltas = replay(&mut state, records, &quarantines, &leases, &limits)?;
    apply_rollups(&mut state, &rollups, &deltas);
    apply_anchors(&mut state, &anchors);

    if let Some(parent) = out_path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::write(out_path, render_json(&state)).map_err(|e| e.to_string())?;
    Ok(())
}

fn read_snapshot(path: &Path) -> io::Result<State> {
    let mut data = Vec::new();
    fs::File::open(path)?.read_to_end(&mut data)?;
    let mut cur = Cursor::new(&data);
    if cur.take(4)? != b"SST1" {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "bad snapshot magic"));
    }
    let generation = cur.u32_le()?;
    let last_seq = cur.u64_le()?;
    let count = cur.u32_le()?;
    let mut accounts = BTreeMap::new();
    for _ in 0..count {
        let key_len = cur.u8()? as usize;
        let key = String::from_utf8(cur.take(key_len)?.to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid key"))?;
        let bal = cur.i64_le()?;
        if bal < 0 {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "negative snapshot balance"));
        }
        if accounts.insert(key, bal).is_some() {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "duplicate snapshot key"));
        }
    }
    if cur.pos != data.len() {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "trailing snapshot bytes"));
    }
    Ok(State { generation, last_seq, accounts, findings: Vec::new() })
}

fn read_wal(path: &Path) -> io::Result<Vec<Record>> {
    let data = match fs::read(path) {
        Ok(d) => d,
        Err(e) if e.kind() == io::ErrorKind::NotFound => Vec::new(),
        Err(e) => return Err(e),
    };
    let mut out = Vec::new();
    let mut pos = 0usize;
    while pos + 4 <= data.len() {
        if &data[pos..pos + 2] != b"WR" {
            pos += 1;
            continue;
        }
        let len = u16::from_le_bytes([data[pos + 2], data[pos + 3]]) as usize;
        if pos + 4 + len + 4 > data.len() {
            pos += 1;
            continue;
        }
        let payload = &data[pos + 4..pos + 4 + len];
        let stored = u32::from_le_bytes([
            data[pos + 4 + len], data[pos + 5 + len], data[pos + 6 + len], data[pos + 7 + len],
        ]);
        if checksum(payload) == stored {
            if let Some(record) = decode_record(payload) {
                out.push(record);
            }
            pos += 4 + len + 4;
        } else {
            pos += 1;
        }
    }
    Ok(out)
}

fn decode_record(payload: &[u8]) -> Option<Record> {
    let mut cur = Cursor::new(payload);
    let generation = cur.u32_le().ok()?;
    let seq = cur.u64_le().ok()?;
    let op = cur.u8().ok()?;
    let key_len = cur.u8().ok()? as usize;
    let key = String::from_utf8(cur.take(key_len).ok()?.to_vec()).ok()?;
    let value = cur.i64_le().ok()?;
    if cur.pos != payload.len() {
        return None;
    }
    Some(Record { generation, seq, op, key, value })
}

fn read_anchors(path: &Path) -> io::Result<Vec<Anchor>> {
    let data = match fs::read(path) {
        Ok(d) => d,
        Err(e) if e.kind() == io::ErrorKind::NotFound => Vec::new(),
        Err(e) => return Err(e),
    };
    if data.is_empty() {
        return Ok(Vec::new());
    }
    let mut cur = Cursor::new(&data);
    if cur.take(4)? != b"ANC1" {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "bad anchor magic"));
    }
    let count = cur.u32_le()?;
    let mut anchors = Vec::new();
    for _ in 0..count {
        let entry_start = cur.pos;
        let seq = cur.u64_le()?;
        let key_len = cur.u8()? as usize;
        let key = String::from_utf8(cur.take(key_len)?.to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid anchor key"))?;
        let value = cur.i64_le()?;
        let checksum_end = cur.pos;
        let stored = cur.u32_le()?;
        if checksum(&data[entry_start..checksum_end]) != stored {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "bad anchor checksum"));
        }
        anchors.push(Anchor { seq, key, value });
    }
    if cur.pos != data.len() {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "trailing anchor bytes"));
    }
    Ok(anchors)
}

fn read_ranges(path: &Path, magic: &[u8; 4], label: &str) -> io::Result<Vec<RangeKey>> {
    let data = match fs::read(path) {
        Ok(d) => d,
        Err(e) if e.kind() == io::ErrorKind::NotFound => Vec::new(),
        Err(e) => return Err(e),
    };
    if data.is_empty() {
        return Ok(Vec::new());
    }
    let mut cur = Cursor::new(&data);
    if cur.take(4)? != magic {
        return Err(io::Error::new(io::ErrorKind::InvalidData, format!("bad {label} magic")));
    }
    let count = cur.u32_le()?;
    let mut out = Vec::new();
    for _ in 0..count {
        let entry_start = cur.pos;
        let generation = cur.u32_le()?;
        let start_seq = cur.u64_le()?;
        let end_seq = cur.u64_le()?;
        if start_seq > end_seq {
            return Err(io::Error::new(io::ErrorKind::InvalidData, format!("inverted {label} range")));
        }
        let key_len = cur.u8()? as usize;
        let key = String::from_utf8(cur.take(key_len)?.to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, format!("invalid {label} key")))?;
        let checksum_end = cur.pos;
        let stored = cur.u32_le()?;
        if checksum(&data[entry_start..checksum_end]) != stored {
            return Err(io::Error::new(io::ErrorKind::InvalidData, format!("bad {label} checksum")));
        }
        out.push(RangeKey { generation, start_seq, end_seq, key });
    }
    if cur.pos != data.len() {
        return Err(io::Error::new(io::ErrorKind::InvalidData, format!("trailing {label} bytes")));
    }
    Ok(out)
}

fn read_limits(path: &Path) -> io::Result<BTreeMap<String, Limit>> {
    let data = match fs::read(path) {
        Ok(d) => d,
        Err(e) if e.kind() == io::ErrorKind::NotFound => Vec::new(),
        Err(e) => return Err(e),
    };
    if data.is_empty() {
        return Ok(BTreeMap::new());
    }
    let mut cur = Cursor::new(&data);
    if cur.take(4)? != b"LIM1" {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "bad limits magic"));
    }
    let count = cur.u32_le()?;
    let mut limits = BTreeMap::new();
    for _ in 0..count {
        let entry_start = cur.pos;
        let key_len = cur.u8()? as usize;
        let key = String::from_utf8(cur.take(key_len)?.to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid limit key"))?;
        let min = cur.i64_le()?;
        let max = cur.i64_le()?;
        let checksum_end = cur.pos;
        let stored = cur.u32_le()?;
        if min < 0 || max < min {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid balance limit"));
        }
        if checksum(&data[entry_start..checksum_end]) != stored {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "bad limits checksum"));
        }
        if limits.insert(key, Limit { min, max }).is_some() {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "duplicate limit key"));
        }
    }
    if cur.pos != data.len() {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "trailing limits bytes"));
    }
    Ok(limits)
}

fn read_rollups(path: &Path) -> io::Result<Vec<Rollup>> {
    let data = match fs::read(path) {
        Ok(d) => d,
        Err(e) if e.kind() == io::ErrorKind::NotFound => Vec::new(),
        Err(e) => return Err(e),
    };
    if data.is_empty() {
        return Ok(Vec::new());
    }
    let mut cur = Cursor::new(&data);
    if cur.take(4)? != b"RLP1" {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "bad rollup magic"));
    }
    let count = cur.u32_le()?;
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for _ in 0..count {
        let entry_start = cur.pos;
        let generation = cur.u32_le()?;
        let start_seq = cur.u64_le()?;
        let end_seq = cur.u64_le()?;
        if start_seq > end_seq {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "inverted rollup range"));
        }
        let key_len = cur.u8()? as usize;
        let key = String::from_utf8(cur.take(key_len)?.to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid rollup key"))?;
        let expected_delta = cur.i64_le()?;
        let checksum_end = cur.pos;
        let stored = cur.u32_le()?;
        if checksum(&data[entry_start..checksum_end]) != stored {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "bad rollup checksum"));
        }
        if !seen.insert((generation, start_seq, end_seq, key.clone())) {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "duplicate rollup entry"));
        }
        out.push(Rollup { generation, start_seq, end_seq, key, expected_delta });
    }
    if cur.pos != data.len() {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "trailing rollup bytes"));
    }
    Ok(out)
}

fn replay(
    state: &mut State,
    records: Vec<Record>,
    quarantines: &[RangeKey],
    leases: &[RangeKey],
    limits: &BTreeMap<String, Limit>,
) -> Result<HashMap<(u64, String), i64>, String> {
    let snapshot_seq = state.last_seq;
    let mut seen = HashSet::new();
    let mut first = Vec::new();
    for rec in records {
        if rec.generation == state.generation && rec.seq > snapshot_seq && seen.insert(rec.seq) {
            first.push(rec);
        }
    }
    first.sort_by_key(|rec| rec.seq);

    let mut deltas: HashMap<(u64, String), i64> = HashMap::new();
    for rec in first {
        if in_range(&rec, quarantines) {
            state.findings.push(format!("quarantined:{}:{}", rec.seq, rec.key));
            continue;
        }
        if matches!(rec.op, 1 | 2 | 3) && in_range(&rec, leases) {
            state.findings.push(format!("leased:{}:{}", rec.seq, rec.key));
            continue;
        }
        match rec.op {
            1 => {
                if rec.value < 0 {
                    return Err(format!("negative balance at sequence {}", rec.seq));
                }
                enforce_limit(&rec.key, rec.value, limits, rec.seq)?;
                let before = *state.accounts.get(&rec.key).unwrap_or(&0);
                state.accounts.insert(rec.key.clone(), rec.value);
                add_delta(&mut deltas, rec.seq, &rec.key, rec.value.checked_sub(before)
                    .ok_or_else(|| format!("balance delta overflow at sequence {}", rec.seq))?)?;
            }
            2 => {
                let current = *state.accounts.get(&rec.key).unwrap_or(&0);
                let next = current.checked_add(rec.value)
                    .ok_or_else(|| format!("balance overflow at sequence {}", rec.seq))?;
                if next < 0 {
                    return Err(format!("negative balance at sequence {}", rec.seq));
                }
                enforce_limit(&rec.key, next, limits, rec.seq)?;
                state.accounts.insert(rec.key.clone(), next);
                add_delta(&mut deltas, rec.seq, &rec.key, rec.value)?;
            }
            3 => {
                if let Some(before) = state.accounts.remove(&rec.key) {
                    add_delta(&mut deltas, rec.seq, &rec.key, before.checked_neg()
                        .ok_or_else(|| format!("balance delta overflow at sequence {}", rec.seq))?)?;
                } else {
                    state.findings.push(format!("missing-delete:{}", rec.seq));
                }
            }
            4 => {
                if rec.key == "_total" {
                    let mut total = 0i64;
                    for value in state.accounts.values() {
                        total = total.checked_add(*value)
                            .ok_or_else(|| format!("checkpoint overflow at sequence {}", rec.seq))?;
                    }
                    if total != rec.value {
                        state.findings.push(format!("checkpoint-mismatch:{}", rec.seq));
                    }
                } else {
                    state.findings.push(format!("ignored-op:{}", rec.seq));
                }
            }
            _ => state.findings.push(format!("ignored-op:{}", rec.seq)),
        }
        state.last_seq = rec.seq;
    }
    Ok(deltas)
}

fn in_range(rec: &Record, ranges: &[RangeKey]) -> bool {
    ranges.iter().any(|r| {
        r.generation == rec.generation && r.key == rec.key && rec.seq >= r.start_seq && rec.seq <= r.end_seq
    })
}

fn add_delta(deltas: &mut HashMap<(u64, String), i64>, seq: u64, key: &str, delta: i64) -> Result<(), String> {
    let entry = deltas.entry((seq, key.to_string())).or_insert(0);
    *entry = entry.checked_add(delta)
        .ok_or_else(|| format!("rollup delta overflow at sequence {seq}"))?;
    Ok(())
}

fn enforce_limit(key: &str, value: i64, limits: &BTreeMap<String, Limit>, seq: u64) -> Result<(), String> {
    if let Some(limit) = limits.get(key) {
        if value < limit.min || value > limit.max {
            return Err(format!("limit violation at sequence {seq}"));
        }
    }
    Ok(())
}

fn apply_rollups(state: &mut State, rollups: &[Rollup], deltas: &HashMap<(u64, String), i64>) {
    for rollup in rollups {
        if rollup.generation != state.generation {
            continue;
        }
        let mut actual = 0i64;
        for seq in rollup.start_seq..=rollup.end_seq {
            if let Some(delta) = deltas.get(&(seq, rollup.key.clone())) {
                actual = actual.wrapping_add(*delta);
            }
        }
        if actual != rollup.expected_delta {
            state.findings.push(format!(
                "rollup-mismatch:{}:{}:{}",
                rollup.start_seq, rollup.end_seq, rollup.key
            ));
        }
    }
}

fn apply_anchors(state: &mut State, anchors: &[Anchor]) {
    for anchor in anchors {
        if anchor.seq <= state.last_seq {
            if let Some(actual) = state.accounts.get(&anchor.key) {
                if *actual != anchor.value {
                    state.findings.push(format!("anchor-mismatch:{}:{}", anchor.seq, anchor.key));
                }
            }
        }
    }
}

fn checksum(payload: &[u8]) -> u32 {
    payload.iter().fold(0u32, |acc, b| acc.wrapping_add(*b as u32))
}

fn render_json(state: &State) -> String {
    let mut s = String::new();
    s.push_str("{\n");
    s.push_str("  \"verdict\": \"accepted\",\n");
    s.push_str(&format!("  \"generation\": {},\n", state.generation));
    s.push_str(&format!("  \"last_seq\": {},\n", state.last_seq));
    s.push_str("  \"accounts\": {");
    if !state.accounts.is_empty() { s.push('\n'); }
    for (idx, (key, value)) in state.accounts.iter().enumerate() {
        let comma = if idx + 1 == state.accounts.len() { "" } else { "," };
        s.push_str(&format!("    \"{}\": {}{}\n", escape_json(key), value, comma));
    }
    if state.accounts.is_empty() { s.push_str("},\n"); } else { s.push_str("  },\n"); }
    s.push_str("  \"findings\": [");
    for (idx, finding) in state.findings.iter().enumerate() {
        if idx > 0 { s.push_str(", "); }
        s.push_str(&format!("\"{}\"", escape_json(finding)));
    }
    s.push_str("]\n}\n");
    s
}

fn escape_json(input: &str) -> String {
    let mut out = String::new();
    for ch in input.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            c if c.is_control() => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

struct Cursor<'a> { data: &'a [u8], pos: usize }

impl<'a> Cursor<'a> {
    fn new(data: &'a [u8]) -> Self { Cursor { data, pos: 0 } }
    fn take(&mut self, n: usize) -> io::Result<&'a [u8]> {
        if self.pos + n > self.data.len() {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "short buffer"));
        }
        let start = self.pos;
        self.pos += n;
        Ok(&self.data[start..start + n])
    }
    fn u8(&mut self) -> io::Result<u8> { Ok(self.take(1)?[0]) }
    fn u32_le(&mut self) -> io::Result<u32> {
        let b = self.take(4)?;
        Ok(u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
    }
    fn u64_le(&mut self) -> io::Result<u64> {
        let b = self.take(8)?;
        Ok(u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]))
    }
    fn i64_le(&mut self) -> io::Result<i64> {
        let b = self.take(8)?;
        Ok(i64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]))
    }
}
RS

cargo build --release --offline
