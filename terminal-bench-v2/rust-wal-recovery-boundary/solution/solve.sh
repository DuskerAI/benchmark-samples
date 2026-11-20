#!/bin/bash
set -euo pipefail

cd /app/task_file

python3 - <<'PY'
from pathlib import Path

Path("src/main.rs").write_text(r'''use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io;
use std::path::Path;

const MAGIC: &[u8; 8] = b"BWL2SEG\n";
const SEGMENT_HEADER_LEN: usize = 24;
const FRAME_HEADER_LEN: usize = 20;
const MAX_PAYLOAD_LEN: usize = 1 << 20;

#[derive(Debug, Clone)]
struct Frame {
    seq: u64,
    segment_id: u64,
    offset: usize,
    op: u8,
    key: String,
    value: String,
    snapshot: Vec<(String, String)>,
}

fn main() {
    if let Err(err) = run() {
        eprintln!("{err}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = env::args().skip(1);
    let Some(cmd) = args.next() else {
        return Err("usage: walfix replay [--json] <segments...>".into());
    };
    if cmd != "replay" {
        return Err(format!("unknown command: {cmd}").into());
    }

    let mut paths = Vec::new();
    for arg in args {
        if arg != "--json" {
            paths.push(arg);
        }
    }
    if paths.is_empty() {
        return Err("at least one segment path is required".into());
    }

    let json = replay(&paths)?;
    println!("{json}");
    Ok(())
}

fn replay(paths: &[String]) -> io::Result<String> {
    let mut all_frames = Vec::new();
    for path in paths {
        all_frames.extend(decode_segment(path)?);
    }
    all_frames.sort_by_key(|frame| (frame.seq, frame.segment_id, frame.offset));

    let mut state = BTreeMap::<String, String>::new();
    for frame in all_frames {
        match frame.op {
            1 => {
                state.insert(frame.key, frame.value);
            }
            2 => {
                state.remove(&frame.key);
            }
            3 => {
                state.clear();
                for (key, value) in frame.snapshot {
                    state.insert(key, value);
                }
            }
            _ => unreachable!("invalid op filtered during decode"),
        }
    }

    Ok(to_json(&state))
}

fn decode_segment(path: impl AsRef<Path>) -> io::Result<Vec<Frame>> {
    let data = fs::read(path)?;
    if data.len() < SEGMENT_HEADER_LEN || &data[..8] != MAGIC {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "bad segment header"));
    }

    let segment_id = read_u64(&data[8..16]);
    let base_seq = read_u64(&data[16..24]);
    let mut pos = SEGMENT_HEADER_LEN;
    let mut frames = Vec::new();

    while pos < data.len() {
        if data.len() - pos < FRAME_HEADER_LEN {
            break;
        }

        let header = &data[pos..pos + FRAME_HEADER_LEN];
        let payload_len = read_u32(&header[0..4]) as usize;
        let seq = read_u64(&header[4..12]);
        let op = header[12];
        let flags = header[13];
        let key_len = read_u16(&header[14..16]) as usize;
        let want_crc = read_u32(&header[16..20]);

        if payload_len > MAX_PAYLOAD_LEN || seq < base_seq || !matches!(op, 1 | 2 | 3) {
            break;
        }

        if matches!(op, 1 | 2) && (key_len == 0 || key_len > payload_len) {
            break;
        }

        if op == 3 && key_len != 0 {
            break;
        }

        let payload_start = pos + FRAME_HEADER_LEN;
        let payload_end = payload_start + payload_len;
        if payload_end > data.len() {
            break;
        }

        let payload = &data[payload_start..payload_end];
        let mut crc_input = Vec::with_capacity(16 + payload.len());
        crc_input.extend_from_slice(&header[..16]);
        crc_input.extend_from_slice(payload);
        if crc32(&crc_input) != want_crc {
            break;
        }

        let (key, value, snapshot) = if op == 3 {
            let Some(snapshot) = parse_snapshot(payload) else {
                break;
            };
            (String::new(), String::new(), snapshot)
        } else {
            let key = match String::from_utf8(payload[..key_len].to_vec()) {
                Ok(key) => key,
                Err(_) => break,
            };
            let value = match String::from_utf8(payload[key_len..].to_vec()) {
                Ok(value) => value,
                Err(_) => break,
            };
            (key, value, Vec::new())
        };

        if flags & 1 != 0 {
            frames.push(Frame {
                seq,
                segment_id,
                offset: pos,
                op,
                key,
                value,
                snapshot,
            });
        }

        pos = payload_end;
    }

    Ok(frames)
}

fn parse_snapshot(payload: &[u8]) -> Option<Vec<(String, String)>> {
    if payload.len() < 2 {
        return None;
    }
    let count = read_u16(&payload[0..2]) as usize;
    let mut pos = 2usize;
    let mut entries = Vec::with_capacity(count);
    for _ in 0..count {
        if payload.len().checked_sub(pos)? < 6 {
            return None;
        }
        let key_len = read_u16(&payload[pos..pos + 2]) as usize;
        let value_len = read_u32(&payload[pos + 2..pos + 6]) as usize;
        pos += 6;
        if key_len == 0 || payload.len().checked_sub(pos)? < key_len.checked_add(value_len)? {
            return None;
        }
        let key_end = pos + key_len;
        let value_end = key_end + value_len;
        let key = String::from_utf8(payload[pos..key_end].to_vec()).ok()?;
        let value = String::from_utf8(payload[key_end..value_end].to_vec()).ok()?;
        entries.push((key, value));
        pos = value_end;
    }
    if pos != payload.len() {
        return None;
    }
    Some(entries)
}

fn read_u16(bytes: &[u8]) -> u16 {
    u16::from_le_bytes(bytes.try_into().expect("u16 slice"))
}

fn read_u32(bytes: &[u8]) -> u32 {
    u32::from_le_bytes(bytes.try_into().expect("u32 slice"))
}

fn read_u64(bytes: &[u8]) -> u64 {
    u64::from_le_bytes(bytes.try_into().expect("u64 slice"))
}

fn crc32(bytes: &[u8]) -> u32 {
    let mut crc = 0xffff_ffffu32;
    for &byte in bytes {
        crc ^= byte as u32;
        for _ in 0..8 {
            let mask = 0u32.wrapping_sub(crc & 1);
            crc = (crc >> 1) ^ (0xedb8_8320 & mask);
        }
    }
    !crc
}

fn to_json(state: &BTreeMap<String, String>) -> String {
    let mut out = String::from("{");
    for (idx, (key, value)) in state.iter().enumerate() {
        if idx != 0 {
            out.push(',');
        }
        out.push('"');
        out.push_str(&escape_json(key));
        out.push_str("\":\"");
        out.push_str(&escape_json(value));
        out.push('"');
    }
    out.push('}');
    out
}

fn escape_json(s: &str) -> String {
    let mut escaped = String::new();
    for ch in s.chars() {
        match ch {
            '"' => escaped.push_str("\\\""),
            '\\' => escaped.push_str("\\\\"),
            '\n' => escaped.push_str("\\n"),
            '\r' => escaped.push_str("\\r"),
            '\t' => escaped.push_str("\\t"),
            c if c.is_control() => escaped.push_str(&format!("\\u{:04x}", c as u32)),
            c => escaped.push(c),
        }
    }
    escaped
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_output_is_sorted_and_escaped() {
        let mut state = BTreeMap::new();
        state.insert("z".to_string(), "line\n".to_string());
        state.insert("a".to_string(), "\"q\"".to_string());
        assert_eq!(to_json(&state), "{\"a\":\"\\\"q\\\"\",\"z\":\"line\\n\"}");
    }

    #[test]
    fn crc32_matches_ieee_vector() {
        assert_eq!(crc32(b"123456789"), 0xcbf4_3926);
    }
}
''')
PY

cargo test
