#!/bin/bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

path = Path('/opt/nvmez-recover/src/main.rs')
s = path.read_text()

s = s.replace(
"use std::path::{Path, PathBuf};\n\n#[derive(Clone)]",
"use std::path::{Path, PathBuf};\n\n#[derive(Clone, Eq, PartialEq, Ord, PartialOrd)]\nstruct Key(u16, u16, u64, u8, u64, u32, Vec<u8>);\n\n#[derive(Clone)]",
)

s = s.replace(
"        if pos + 36 > bytes.len() {\n            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, \"short record header\"));\n        }",
"        if pos + 36 > bytes.len() { break; }",
)
s = s.replace(
"        if payload_end > bytes.len() {\n            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, \"short payload\"));\n        }",
"        let padded_end = payload_end + ((4 - (len % 4)) % 4);\n        if padded_end > bytes.len() { break; }",
)
s = s.replace("        pos = payload_end;", "        pos = padded_end;")

s = s.replace(
"fn open_count(zones: &[Zone], nsid: u16) -> usize { zones.iter().filter(|z| z.nsid == nsid && z.open).count() }\n\nfn apply",
"fn open_count(zones: &[Zone], nsid: u16) -> usize { zones.iter().filter(|z| z.nsid == nsid && z.open).count() }\n\nfn snapshot(zones: &[Zone]) -> Vec<(u64, Vec<u8>, bool)> { zones.iter().map(|z| (z.wp, z.data.clone(), z.open)).collect() }\n\nfn restore(zones: &mut [Zone], snap: &[(u64, Vec<u8>, bool)]) {\n    for (z, (wp, data, open)) in zones.iter_mut().zip(snap.iter()) {\n        z.wp = *wp;\n        z.data.clone_from(data);\n        z.open = *open;\n    }\n}\n\nfn apply",
)
s = s.replace(
"    let mut zones = load_geometry(&case.join(\"geometry.tsv\"))?;\n    let recs = parse_stream(&case.join(\"stream.ncap\"))?;",
"    let mut zones = load_geometry(&case.join(\"geometry.tsv\"))?;\n    let initial = snapshot(&zones);\n    let recs = parse_stream(&case.join(\"stream.ncap\"))?;\n    let mut persisted: Option<Vec<(u64, Vec<u8>, bool)>> = None;",
)
s = s.replace("    let mut seen_cids: BTreeSet<u16> = BTreeSet::new();", "    let mut accepted: BTreeSet<Key> = BTreeSet::new();")
s = s.replace(
"            stats.cut_seq = Some(r.seq);\n            audit.push(format!(\"{},{},{},crash,{},{},{},cut\", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));\n            break;",
"            stats.cut_seq = Some(r.seq);\n            audit.push(format!(\"{},{},{},crash,{},{},{},cut\", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));\n            let snap = persisted.as_ref().unwrap_or(&initial);\n            restore(&mut zones, snap);\n            break;",
)
s = s.replace(
"            stats.flushes += 1;\n            audit.push(format!(\"{},{},{},flush,{},{},{},ok\", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));",
"            stats.flushes += 1;\n            persisted = Some(snapshot(&zones));\n            audit.push(format!(\"{},{},{},flush,{},{},{},ok\", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));",
)
s = s.replace(
"        if !seen_cids.insert(r.cid) {\n            stats.duplicates += 1;\n            audit.push(format!(\"{},{},{},duplicate,{},{},{},seen\", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));\n            continue;\n        }",
"        let key = Key(r.nsid, r.cid, r.seq, r.rtype, r.lba, r.nlb, r.payload.clone());\n        if accepted.contains(&key) {\n            stats.duplicates += 1;\n            audit.push(format!(\"{},{},{},duplicate,{},{},{},seen\", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));\n            continue;\n        }",
)
s = s.replace(
"        if r.rtype == 3 {\n            zones[idx].wp = zones[idx].zslba;\n            zones[idx].open = false;\n            stats.applied += 1;",
"        if r.rtype == 3 {\n            zones[idx].data.fill(0);\n            zones[idx].wp = zones[idx].zslba;\n            zones[idx].open = false;\n            accepted.insert(key);\n            stats.applied += 1;",
)
s = s.replace("        let start_lba = r.lba;", "        let start_lba = if r.rtype == 2 { zones[idx].wp } else { r.lba };")
s = s.replace(
"        if !zones[idx].open && open_count(&zones, r.nsid) > zones[idx].max_open {\n            stats.ignored += 1;\n            audit.push(format!(\"{},{},{},ignored,{},{},{},limit\", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));",
"        if !zones[idx].open && open_count(&zones, r.nsid) >= zones[idx].max_open {\n            stats.ignored += 1;\n            audit.push(format!(\"{},{},{},ignored,{},{},{},state\", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));",
)
s = s.replace(
"        stats.applied += 1;\n        audit.push(format!(\"{},{},{},applied,{},{},{},ok\", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));",
"        accepted.insert(key);\n        stats.applied += 1;\n        audit.push(format!(\"{},{},{},applied,{},{},{},ok\", r.seq, r.path, r.cid, r.nsid, r.lba, r.nlb));",
)

path.write_text(s)
PY

cd /opt/nvmez-recover
cargo build --release
