use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::io::{self, Read};
use std::path::Path;

#[derive(Clone, Debug)]
struct State {
    generation: u32,
    last_seq: u64,
    accounts: BTreeMap<String, i64>,
    warnings: Vec<String>,
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
struct Quarantine {
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
    let snapshot = state_dir.join("snapshot.bin");
    let wal = state_dir.join("wal.log");
    let anchors = state_dir.join("anchors.bin");
    let quarantine = state_dir.join("quarantine.bin");
    let limits = state_dir.join("limits.bin");

    let mut state = read_snapshot(&snapshot).map_err(|e| format!("snapshot: {e}"))?;
    let records = read_wal(&wal).map_err(|e| format!("wal: {e}"))?;
    let anchors = read_anchors(&anchors).map_err(|e| format!("anchors: {e}"))?;
    let quarantines = read_quarantine(&quarantine).map_err(|e| format!("quarantine: {e}"))?;
    let leases = read_leases(&state_dir.join("leases.bin")).map_err(|e| format!("leases: {e}"))?;
    let limits = read_limits(&limits).map_err(|e| format!("limits: {e}"))?;
    validate_anchors(&mut state, &anchors);
    replay(&mut state, records, &limits)?;
    apply_lease_findings(&mut state, &leases);
    apply_quarantine_findings(&mut state, &quarantines);

    if let Some(parent) = out_path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::write(out_path, render_json(&state)).map_err(|e| e.to_string())?;
    Ok(())
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
    let mut out = BTreeMap::new();
    for _ in 0..count {
        let key_len = cur.u8()? as usize;
        let key = String::from_utf8(cur.take(key_len)?.to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid limit key"))?;
        let min = cur.i64_le()?;
        let max = cur.i64_le()?;
        let _checksum = cur.u32_le()?;
        out.insert(key, Limit { min, max });
    }
    Ok(out)
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
        let seq_bytes = cur.take(8)?;
        let seq = u64::from_be_bytes([
            seq_bytes[0], seq_bytes[1], seq_bytes[2], seq_bytes[3],
            seq_bytes[4], seq_bytes[5], seq_bytes[6], seq_bytes[7],
        ]);
        let key_len = cur.u8()? as usize;
        let key = String::from_utf8(cur.take(key_len)?.to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid anchor key"))?;
        let value = cur.i64_le()?;
        let _checksum = cur.u32_le()?;
        anchors.push(Anchor { seq, key, value });
    }
    Ok(anchors)
}

fn read_quarantine(path: &Path) -> io::Result<Vec<Quarantine>> {
    let data = match fs::read(path) {
        Ok(d) => d,
        Err(e) if e.kind() == io::ErrorKind::NotFound => Vec::new(),
        Err(e) => return Err(e),
    };
    if data.is_empty() {
        return Ok(Vec::new());
    }
    let mut cur = Cursor::new(&data);
    if cur.take(4)? != b"QRN1" {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "bad quarantine magic"));
    }
    let count = cur.u32_le()?;
    let mut out = Vec::new();
    for _ in 0..count {
        let generation = cur.u32_le()?;
        let start_seq = cur.u64_le()?;
        let end_seq = cur.u64_le()?;
        let key_len = cur.u8()? as usize;
        let key = String::from_utf8(cur.take(key_len)?.to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid quarantine key"))?;
        let _checksum = cur.u32_le()?;
        out.push(Quarantine { generation, start_seq, end_seq, key });
    }
    Ok(out)
}

fn apply_quarantine_findings(state: &mut State, quarantines: &[Quarantine]) {
    for q in quarantines {
        if q.generation == state.generation && q.start_seq <= state.last_seq && q.end_seq >= state.last_seq {
            state.warnings.push(format!("quarantined:{}:{}", state.last_seq, q.key));
        }
    }
}

fn read_leases(path: &Path) -> io::Result<Vec<Quarantine>> {
    let data = match fs::read(path) {
        Ok(d) => d,
        Err(e) if e.kind() == io::ErrorKind::NotFound => Vec::new(),
        Err(e) => return Err(e),
    };
    if data.is_empty() {
        return Ok(Vec::new());
    }
    let mut cur = Cursor::new(&data);
    if cur.take(4)? != b"LSE1" {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "bad lease magic"));
    }
    let count = cur.u32_le()?;
    let mut out = Vec::new();
    for _ in 0..count {
        let generation = cur.u32_le()?;
        let start_seq = cur.u64_le()?;
        let end_seq = cur.u64_le()?;
        let key_len = cur.u8()? as usize;
        let key = String::from_utf8(cur.take(key_len)?.to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid lease key"))?;
        let _checksum = cur.u32_le()?;
        out.push(Quarantine { generation, start_seq, end_seq, key });
    }
    Ok(out)
}

fn apply_lease_findings(state: &mut State, leases: &[Quarantine]) {
    for lease in leases {
        if lease.generation == state.generation && lease.start_seq <= state.last_seq && lease.end_seq >= state.last_seq {
            state.warnings.push(format!("leased:{}:{}", state.last_seq, lease.key));
        }
    }
}

fn validate_anchors(state: &mut State, anchors: &[Anchor]) {
    for anchor in anchors {
        if anchor.seq <= state.last_seq {
            if let Some(actual) = state.accounts.get(&anchor.key) {
                if *actual != anchor.value {
                    state.warnings.push(format!("anchor-mismatch:{}:{}", anchor.seq, anchor.key));
                }
            }
        }
    }
}

fn read_snapshot(path: &Path) -> io::Result<State> {
    let mut data = Vec::new();
    fs::File::open(path)?.read_to_end(&mut data)?;
    let mut cur = Cursor::new(&data);
    let magic = cur.take(4)?;
    if magic != b"SST1" {
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
        accounts.insert(key, bal);
    }
    Ok(State { generation, last_seq, accounts, warnings: Vec::new() })
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
            break;
        }
        let len = u16::from_be_bytes([data[pos + 2], data[pos + 3]]) as usize;
        if pos + 4 + len + 4 > data.len() {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "truncated record"));
        }
        let payload = &data[pos + 4..pos + 4 + len];
        let stored = u32::from_le_bytes([
            data[pos + 4 + len],
            data[pos + 5 + len],
            data[pos + 6 + len],
            data[pos + 7 + len],
        ]);
        if checksum(payload) != stored {
            break;
        }
        if let Some(record) = decode_record(payload) {
            out.push(record);
        }
        pos += 4 + len + 4;
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
    Some(Record { generation, seq, op, key, value })
}

fn replay(state: &mut State, records: Vec<Record>, limits: &BTreeMap<String, Limit>) -> Result<(), String> {
    let mut seen = BTreeSet::new();
    for rec in records {
        if !seen.insert(rec.seq) {
            continue;
        }
        if rec.seq <= state.last_seq {
            continue;
        }
        match rec.op {
            1 => {
                if let Some(limit) = limits.get(&rec.key) {
                    if rec.value < limit.min || rec.value > limit.max {
                        return Err(format!("limit violation at sequence {}", rec.seq));
                    }
                }
                state.accounts.insert(rec.key, rec.value);
            }
            2 => {
                let entry = state.accounts.entry(rec.key.clone()).or_insert(0);
                *entry += rec.value;
                if let Some(limit) = limits.get(&rec.key) {
                    if *entry < limit.min || *entry > limit.max {
                        return Err(format!("limit violation at sequence {}", rec.seq));
                    }
                }
            }
            3 => {
                state.accounts.remove(&rec.key);
            }
            _ => state.warnings.push(format!("ignored-op:{}", rec.seq)),
        }
        state.last_seq = rec.seq;
    }
    Ok(())
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
    if !state.accounts.is_empty() {
        s.push('\n');
    }
    for (idx, (key, value)) in state.accounts.iter().enumerate() {
        let comma = if idx + 1 == state.accounts.len() { "" } else { "," };
        s.push_str(&format!("    \"{}\": {}{}\n", escape_json(key), value, comma));
    }
    if state.accounts.is_empty() {
        s.push_str("},\n");
    } else {
        s.push_str("  },\n");
    }
    s.push_str("  \"findings\": [");
    for (idx, warn) in state.warnings.iter().enumerate() {
        if idx > 0 { s.push_str(", "); }
        s.push_str(&format!("\"{}\"", escape_json(warn)));
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

struct Cursor<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Cursor<'a> {
    fn new(data: &'a [u8]) -> Self {
        Cursor { data, pos: 0 }
    }

    fn take(&mut self, n: usize) -> io::Result<&'a [u8]> {
        if self.pos + n > self.data.len() {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "short buffer"));
        }
        let start = self.pos;
        self.pos += n;
        Ok(&self.data[start..start + n])
    }

    fn u8(&mut self) -> io::Result<u8> {
        Ok(self.take(1)?[0])
    }

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
