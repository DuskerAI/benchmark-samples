// Replay contract for nvmez-recover:
// geometry.tsv columns are nsid, zslba, capacity, block_size, max_open. Zones
// start empty at write pointer zslba. stream.ncap records are NCAP, rtype:u8,
// flags:u8, cid:u16le, nsid:u16le, path:u8, status:u8, lba:u64le, nlb:u32le,
// seq:u64le, payload_len:u32le, payload, then zero padding to a 4-byte boundary;
// incomplete EOF header, payload, or padding is ignored. Types are 1=WRITE,
// 2=ZONE_APPEND, 3=RESET, 4=FLUSH, 5=ANA, 6=CRASH_CUT. A path with no prior
// ANA record is online; ANA sets a path online when flags&1, offline otherwise. Nonzero-status or
// offline-path namespace commands are ignored. Duplicate namespace commands are
// successful commands with the same namespace, cid, seq, type, starting LBA,
// block count, and payload; cid reuse by another command is not a duplicate.
// WRITE starts at the zone write pointer with nlb*block_size payload bytes.
// ZONE_APPEND selects the zone containing the header LBA and writes at that
// zone's current write pointer. RESET zeroes a zone and returns wp to zslba.
// A namespace may not exceed max_open open zones. FLUSH persists current state.
// CRASH_CUT ends replay; output the most recent flushed state before the cut, or
// final state when there is no crash. Outputs are exactly zones.jsonl, audit.csv,
// manifest.json. zones.jsonl is sorted by nsid,zslba with nsid,zslba,wp,digest,
// ranges; digest is FNV-1a64 lowercase hex and ranges are nonzero inclusive block
// ranges. audit.csv header is seq,path,cid,event,nsid,lba,nlb,result and includes
// complete records through the first CRASH_CUT, including that row. Vocabulary:
// ANA ana/ok; FLUSH flush/ok; CRASH_CUT crash/cut; duplicate duplicate/seen;
// RESET reset/ok; WRITE or ZONE_APPEND applied/ok; nonzero status or offline path
// ignored/path; LBA outside zones ignored/range; other namespace-state rejection,
// including bad payload length, wrong write pointer, capacity overflow,
// unsupported namespace command type, or exceeding max_open, ignored/state.
// manifest.json key order is case,records,applied,duplicates,ignored,flushes,
// cut_seq,zones. records counts complete parsed records through CRASH_CUT if
// present, never torn tails. The manifest applied field is not the audit event
// name count: it counts successful namespace-modifying WRITE, ZONE_APPEND, and
// RESET commands. cut_seq is the CRASH_CUT seq or null; zones is geometry row count.

use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

#[derive(Clone)]
struct Rec {
    rtype: u8,
    flags: u8,
    cid: u16,
    nsid: u16,
    path: u8,
    status: u8,
    lba: u64,
    nlb: u32,
    seq: u64,
    payload: Vec<u8>,
}

#[derive(Clone)]
struct Zone {
    nsid: u16,
    zslba: u64,
    capacity: u64,
    block_size: usize,
    max_open: usize,
    wp: u64,
    data: Vec<u8>,
    open: bool,
}

#[derive(Default, Clone)]
struct Stats {
    records: u64,
    applied: u64,
    duplicates: u64,
    ignored: u64,
    flushes: u64,
    cut_seq: Option<u64>,
}

fn le_u16(b: &[u8]) -> u16 { u16::from_le_bytes([b[0], b[1]]) }
fn le_u32(b: &[u8]) -> u32 { u32::from_le_bytes([b[0], b[1], b[2], b[3]]) }
fn le_u64(b: &[u8]) -> u64 { u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]) }

fn parse_stream(path: &Path) -> io::Result<Vec<Rec>> {
    let bytes = fs::read(path)?;
    let mut out = Vec::new();
    let mut pos = 0usize;
    while pos < bytes.len() {
        if pos + 36 > bytes.len() {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "short record header"));
        }
        if &bytes[pos..pos + 4] != b"NCAP" {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "bad magic"));
        }
        let h = &bytes[pos..pos + 36];
        let len = le_u32(&h[32..36]) as usize;
        let payload_start = pos + 36;
        let payload_end = payload_start + len;
        if payload_end > bytes.len() {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "short payload"));
        }
        out.push(Rec {
            rtype: h[4], flags: h[5], cid: le_u16(&h[6..8]), nsid: le_u16(&h[8..10]), path: h[10], status: h[11],
            lba: le_u64(&h[12..20]), nlb: le_u32(&h[20..24]), seq: le_u64(&h[24..32]),
            payload: bytes[payload_start..payload_end].to_vec(),
        });
        pos = payload_end;
    }
    Ok(out)
}

fn load_geometry(path: &Path) -> io::Result<Vec<Zone>> {
    let text = fs::read_to_string(path)?;
    let mut zones = Vec::new();
    for line in text.lines().skip(1) {
        if line.trim().is_empty() { continue; }
        let cols: Vec<&str> = line.split('\t').collect();
        if cols.len() != 5 { continue; }
        let nsid: u16 = cols[0].parse().unwrap();
        let zslba: u64 = cols[1].parse().unwrap();
        let capacity: u64 = cols[2].parse().unwrap();
        let block_size: usize = cols[3].parse().unwrap();
        let max_open: usize = cols[4].parse().unwrap();
        zones.push(Zone { nsid, zslba, capacity, block_size, max_open, wp: zslba, data: vec![0; capacity as usize * block_size], open: false });
    }
    zones.sort_by_key(|z| (z.nsid, z.zslba));
    Ok(zones)
}

fn fnv64(data: &[u8]) -> String {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in data { h ^= *b as u64; h = h.wrapping_mul(0x100000001b3); }
    format!("{:016x}", h)
}

fn nonzero_ranges(z: &Zone) -> Vec<String> {
    let mut ranges = Vec::new();
    let mut start: Option<u64> = None;
    for blk in 0..z.capacity {
        let a = blk as usize * z.block_size;
        let nz = z.data[a..a + z.block_size].iter().any(|b| *b != 0);
        match (start, nz) {
            (None, true) => start = Some(blk),
            (Some(s), false) => { ranges.push(if s == blk - 1 { format!("{}", z.zslba + s) } else { format!("{}-{}", z.zslba + s, z.zslba + blk - 1) }); start = None; }
            _ => {}
        }
    }
    if let Some(s) = start {
        let e = z.capacity - 1;
        ranges.push(if s == e { format!("{}", z.zslba + s) } else { format!("{}-{}", z.zslba + s, z.zslba + e) });
    }
    ranges
}

fn find_zone(zones: &mut [Zone], nsid: u16, lba: u64) -> Option<usize> {
    zones.iter().position(|z| z.nsid == nsid && lba >= z.zslba && lba < z.zslba + z.capacity)
}

fn open_count(zones: &[Zone], nsid: u16) -> usize { zones.iter().filter(|z| z.nsid == nsid && z.open).count() }

fn apply(case: &Path) -> io::Result<(Vec<Zone>, Vec<String>, Stats)> {
    let mut zones = load_geometry(&case.join("geometry.tsv"))?;
    let recs = parse_stream(&case.join("stream.ncap"))?;
    let mut stats = Stats::default();
    let mut audit = vec!["seq,path,cid,event,nsid,lba,nlb,result".to_string()];
    let mut online: BTreeMap<u8, bool> = BTreeMap::new();
    let mut seen_cids: BTreeSet<u16> = BTreeSet::new();
    for r in recs {
        stats.records += 1;
        if r.rtype == 6 {
            stats.cut_seq = Some(r.seq);
            audit.push(format!("{},{},{},crash,{},{},{},cut", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));
            break;
        }
        if r.rtype == 5 {
            online.insert(r.path, r.flags & 1 == 1);
            audit.push(format!("{},{},{},ana,{},{},{},ok", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));
            continue;
        }
        if r.rtype == 4 {
            stats.flushes += 1;
            audit.push(format!("{},{},{},flush,{},{},{},ok", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));
            continue;
        }
        if r.status != 0 || !online.get(&r.path).copied().unwrap_or(true) {
            stats.ignored += 1;
            audit.push(format!("{},{},{},ignored,{},{},{},path", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));
            continue;
        }
        if !seen_cids.insert(r.cid) {
            stats.duplicates += 1;
            audit.push(format!("{},{},{},duplicate,{},{},{},seen", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));
            continue;
        }
        let Some(idx) = find_zone(&mut zones, r.nsid, r.lba) else {
            stats.ignored += 1;
            audit.push(format!("{},{},{},ignored,{},{},{},range", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));
            continue;
        };
        if r.rtype == 3 {
            zones[idx].wp = zones[idx].zslba;
            zones[idx].open = false;
            stats.applied += 1;
            audit.push(format!("{},{},{},reset,{},{},{},ok", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));
            continue;
        }
        let start_lba = r.lba;
        let blocks = r.nlb as u64;
        let need = blocks as usize * zones[idx].block_size;
        if r.payload.len() != need || start_lba != zones[idx].wp || start_lba + blocks > zones[idx].zslba + zones[idx].capacity {
            stats.ignored += 1;
            audit.push(format!("{},{},{},ignored,{},{},{},state", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));
            continue;
        }
        if !zones[idx].open && open_count(&zones, r.nsid) > zones[idx].max_open {
            stats.ignored += 1;
            audit.push(format!("{},{},{},ignored,{},{},{},limit", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));
            continue;
        }
        let off = ((start_lba - zones[idx].zslba) as usize) * zones[idx].block_size;
        zones[idx].data[off..off + need].copy_from_slice(&r.payload);
        zones[idx].wp += blocks;
        zones[idx].open = zones[idx].wp < zones[idx].zslba + zones[idx].capacity;
        stats.applied += 1;
        audit.push(format!("{},{},{},applied,{},{},{},ok", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));
    }
    Ok((zones, audit, stats))
}

fn write_outputs(case: &Path, out: &Path, zones: &[Zone], audit: &[String], stats: &Stats) -> io::Result<()> {
    fs::create_dir_all(out)?;
    let mut zf = File::create(out.join("zones.jsonl"))?;
    for z in zones {
        let ranges = nonzero_ranges(z).into_iter().map(|r| format!("\"{}\"", r)).collect::<Vec<_>>().join(",");
        writeln!(zf, "{{\"nsid\":{},\"zslba\":{},\"wp\":{},\"digest\":\"{}\",\"ranges\":[{}]}}", z.nsid, z.zslba, z.wp, fnv64(&z.data), ranges)?;
    }
    fs::write(out.join("audit.csv"), audit.join("\n") + "\n")?;
    let case_name = case.file_name().unwrap().to_string_lossy();
    let cut = stats.cut_seq.map(|v| v.to_string()).unwrap_or_else(|| "null".to_string());
    let manifest = format!("{{\"case\":\"{}\",\"records\":{},\"applied\":{},\"duplicates\":{},\"ignored\":{},\"flushes\":{},\"cut_seq\":{},\"zones\":{}}}\n", case_name, stats.records, stats.applied, stats.duplicates, stats.ignored, stats.flushes, cut, zones.len());
    fs::write(out.join("manifest.json"), manifest)?;
    Ok(())
}

fn main() -> io::Result<()> {
    let args: Vec<String> = env::args().collect();
    let mut case = None::<PathBuf>;
    let mut out = None::<PathBuf>;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--case" if i + 1 < args.len() => { case = Some(PathBuf::from(&args[i + 1])); i += 2; }
            "--out" if i + 1 < args.len() => { out = Some(PathBuf::from(&args[i + 1])); i += 2; }
            _ => { eprintln!("usage: nvmez-recover --case <case-dir> --out <output-dir>"); std::process::exit(2); }
        }
    }
    let case = case.expect("missing --case");
    let out = out.expect("missing --out");
    let (zones, audit, stats) = apply(&case)?;
    write_outputs(&case, &out, &zones, &audit, &stats)
}
