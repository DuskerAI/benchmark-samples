use std::collections::BTreeMap;
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
    flags: u8,
    key: String,
    value: String,
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
    let mut state = BTreeMap::<String, String>::new();

    for path in paths {
        for frame in decode_segment(path)? {
            if frame.op == 1 {
                state.insert(frame.key, frame.value);
            } else if frame.op == 2 {
                state.remove(&frame.key);
            }
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
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "truncated frame header"));
        }

        let header = &data[pos..pos + FRAME_HEADER_LEN];
        let payload_len = read_u32(&header[0..4]) as usize;
        let seq = read_u64(&header[4..12]);
        let op = header[12];
        let flags = header[13];
        let key_len = read_u16(&header[14..16]) as usize;
        let want_crc = read_u32(&header[16..20]);

        if payload_len > MAX_PAYLOAD_LEN || key_len == 0 || key_len > payload_len || seq < base_seq {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid frame"));
        }

        let payload_start = pos + FRAME_HEADER_LEN;
        let payload_end = payload_start + payload_len;
        if payload_end > data.len() {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "truncated frame payload"));
        }

        let payload = &data[payload_start..payload_end];
        let got_crc = crc32(payload);
        if got_crc != want_crc {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "checksum mismatch"));
        }

        let key = String::from_utf8(payload[..key_len].to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "key is not utf-8"))?;
        let value = String::from_utf8(payload[key_len..].to_vec())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "value is not utf-8"))?;

        frames.push(Frame {
            seq,
            segment_id,
            offset: pos,
            op,
            flags,
            key,
            value,
        });

        pos = payload_end;
    }

    Ok(frames)
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
