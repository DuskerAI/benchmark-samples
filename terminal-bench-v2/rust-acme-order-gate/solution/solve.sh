#!/bin/bash
set -euo pipefail

cat > /app/acmegate/src/main.rs <<'RS'
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::env;
use std::fs;
use std::path::Path;

#[derive(Clone)]
struct Account { key: String, active: bool }

#[derive(Clone)]
struct OrderState { account: String, status: String, expires: i64 }

#[derive(Clone)]
struct ChallengeState { account: String, _token: String, not_before: i64, expires: i64, status: String }

#[derive(Clone)]
struct Evidence {
    line: usize,
    order: String,
    account: String,
    nonce: String,
    challenge: String,
    protected: String,
    payload: String,
    signature: String,
}

struct State {
    nonces: HashSet<String>,
    orders: BTreeMap<String, OrderState>,
    challenges: BTreeMap<String, ChallengeState>,
}

struct Decision {
    line: usize,
    order: String,
    account: String,
    nonce: String,
    decision: String,
    reasons: Vec<String>,
}

fn main() {
    let result = run();
    if let Err(msg) = result {
        eprintln!("{}", msg);
        std::process::exit(2);
    }
}

fn run() -> Result<(), String> {
    let args: Vec<String> = env::args().collect();
    let mut flags = BTreeMap::new();
    let mut i = 1;
    while i < args.len() {
        if !args[i].starts_with("--") || i + 1 >= args.len() {
            return Err("usage: acmegate --trust TRUST --state STATE --bundle BUNDLE --out REPORT --now UNIX".to_string());
        }
        flags.insert(args[i].clone(), args[i + 1].clone());
        i += 2;
    }
    let trust_path = need(&flags, "--trust")?;
    let state_path = need(&flags, "--state")?;
    let bundle_path = need(&flags, "--bundle")?;
    let out_path = need(&flags, "--out")?;
    let now: i64 = need(&flags, "--now")?.parse().map_err(|_| "invalid --now".to_string())?;

    let accounts = read_trust(trust_path)?;
    let state = read_state(state_path)?;
    let evidence = read_evidence(bundle_path)?;
    let mut seen = HashSet::new();
    let mut accepted_nonces = BTreeSet::new();
    let mut ready_orders = BTreeSet::new();
    let mut decisions = Vec::new();

    for rec in evidence {
        let mut reasons = Vec::new();
        let account_ok = accounts.get(&rec.account).map(|a| a.active).unwrap_or(false);
        if !account_ok { reasons.push("BAD_ACCOUNT".to_string()); }

        let expected_protected = canonical_protected(&rec.account, &rec.nonce, &rec.order);
        let decoded_protected = b64url_decode_to_string(&rec.protected).unwrap_or_default();
        if decoded_protected != expected_protected || b64url_encode(expected_protected.as_bytes()) != rec.protected {
            reasons.push("BAD_PROTECTED".to_string());
        }

        let expected_payload = canonical_payload(&rec.account, &rec.challenge, &rec.order);
        let decoded_payload = b64url_decode_to_string(&rec.payload).unwrap_or_default();
        if decoded_payload != expected_payload || b64url_encode(expected_payload.as_bytes()) != rec.payload {
            reasons.push("BAD_PAYLOAD".to_string());
        }

        if let Some(account) = accounts.get(&rec.account) {
            let expected_sig = mac64(&account.key, &rec.protected, &rec.payload);
            if rec.signature != expected_sig { reasons.push("BAD_SIGNATURE".to_string()); }
        } else {
            reasons.push("BAD_SIGNATURE".to_string());
        }

        if state.nonces.contains(&rec.nonce) || seen.contains(&rec.nonce) {
            reasons.push("NONCE_REPLAY".to_string());
        }
        seen.insert(rec.nonce.clone());

        match state.orders.get(&rec.order) {
            Some(order) => {
                if order.account != rec.account || order.status != "pending" || order.expires < now {
                    reasons.push("ORDER_STATE".to_string());
                }
            }
            None => reasons.push("ORDER_STATE".to_string()),
        }

        match state.challenges.get(&rec.challenge) {
            Some(chal) => {
                if chal.account != rec.account || chal.status != "valid" {
                    reasons.push("CHALLENGE_BINDING".to_string());
                }
                if now < chal.not_before || now > chal.expires {
                    reasons.push("CHALLENGE_TIME".to_string());
                }
            }
            None => {
                reasons.push("CHALLENGE_BINDING".to_string());
                reasons.push("CHALLENGE_TIME".to_string());
            }
        }

        dedup_in_order(&mut reasons);
        let decision = if reasons.is_empty() { "ACCEPT" } else { "REJECT" }.to_string();
        if decision == "ACCEPT" {
            accepted_nonces.insert(rec.nonce.clone());
            ready_orders.insert(rec.order.clone());
        }
        decisions.push(Decision { line: rec.line, order: rec.order, account: rec.account, nonce: rec.nonce, decision, reasons });
    }

    let accepted_count = decisions.iter().filter(|d| d.decision == "ACCEPT").count();
    let rejected_count = decisions.len() - accepted_count;
    let report = render_report(accepted_count, rejected_count, &decisions, &accepted_nonces, &ready_orders);
    if let Some(parent) = Path::new(out_path).parent() {
        if !parent.as_os_str().is_empty() { fs::create_dir_all(parent).map_err(|e| e.to_string())?; }
    }
    fs::write(out_path, report).map_err(|e| e.to_string())?;
    Ok(())
}

fn need<'a>(flags: &'a BTreeMap<String, String>, name: &str) -> Result<&'a str, String> {
    flags.get(name).map(|s| s.as_str()).ok_or_else(|| format!("missing {}", name))
}

fn read_trust(path: &str) -> Result<BTreeMap<String, Account>, String> {
    let mut out = BTreeMap::new();
    let text = fs::read_to_string(path).map_err(|e| e.to_string())?;
    for (idx, raw) in text.lines().enumerate() {
        let line = raw.trim_end();
        if line.is_empty() || line.starts_with('#') { continue; }
        let parts: Vec<&str> = line.split('\t').collect();
        if parts.len() != 3 { return Err(format!("bad trust line {}", idx + 1)); }
        out.insert(parts[0].to_string(), Account { key: parts[1].to_string(), active: parts[2] == "active" });
    }
    Ok(out)
}

fn read_state(path: &str) -> Result<State, String> {
    let mut nonces = HashSet::new();
    let mut orders = BTreeMap::new();
    let mut challenges = BTreeMap::new();
    let text = fs::read_to_string(path).map_err(|e| e.to_string())?;
    for (idx, raw) in text.lines().enumerate() {
        let line = raw.trim_end();
        if line.is_empty() || line.starts_with('#') { continue; }
        let parts: Vec<&str> = line.split('\t').collect();
        match parts.get(0).copied() {
            Some("nonce") if parts.len() == 2 => { nonces.insert(parts[1].to_string()); }
            Some("order") if parts.len() == 5 => {
                orders.insert(parts[1].to_string(), OrderState { account: parts[2].to_string(), status: parts[3].to_string(), expires: parse_i64(parts[4], idx)? });
            }
            Some("challenge") if parts.len() == 7 => {
                challenges.insert(parts[1].to_string(), ChallengeState { account: parts[2].to_string(), _token: parts[3].to_string(), not_before: parse_i64(parts[4], idx)?, expires: parse_i64(parts[5], idx)?, status: parts[6].to_string() });
            }
            _ => return Err(format!("bad state line {}", idx + 1)),
        }
    }
    Ok(State { nonces, orders, challenges })
}

fn read_evidence(dir: &str) -> Result<Vec<Evidence>, String> {
    let path = Path::new(dir).join("evidence.tsv");
    let text = fs::read_to_string(path).map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for (idx, raw) in text.lines().enumerate() {
        let line = raw.trim_end();
        if line.is_empty() || line.starts_with('#') { continue; }
        let parts: Vec<&str> = line.split('\t').collect();
        if parts.len() != 7 { return Err(format!("bad evidence line {}", idx + 1)); }
        out.push(Evidence { line: idx + 1, order: parts[0].to_string(), account: parts[1].to_string(), nonce: parts[2].to_string(), challenge: parts[3].to_string(), protected: parts[4].to_string(), payload: parts[5].to_string(), signature: parts[6].to_string() });
    }
    Ok(out)
}

fn parse_i64(s: &str, idx: usize) -> Result<i64, String> { s.parse().map_err(|_| format!("bad integer on line {}", idx + 1)) }

fn canonical_protected(account: &str, nonce: &str, order: &str) -> String {
    format!("{{\"alg\":\"ACME-MAC64\",\"kid\":\"{}\",\"nonce\":\"{}\",\"url\":\"https://ca.local/acme/order/{}\"}}", account, nonce, order)
}

fn canonical_payload(account: &str, challenge: &str, order: &str) -> String {
    format!("{{\"account\":\"{}\",\"challenge\":\"{}\",\"order\":\"{}\",\"status\":\"ready\"}}", account, challenge, order)
}

fn mac64(key: &str, protected: &str, payload: &str) -> String {
    let mut h: u64 = 14695981039346656037;
    let mut feed = |bytes: &[u8]| {
        for b in bytes { h ^= *b as u64; h = h.wrapping_mul(1099511628211); }
    };
    feed(key.as_bytes()); feed(b"\n"); feed(protected.as_bytes()); feed(b"."); feed(payload.as_bytes());
    format!("{:016x}", h)
}

fn b64url_decode_to_string(s: &str) -> Option<String> {
    let bytes = b64url_decode(s)?;
    String::from_utf8(bytes).ok()
}

fn b64url_decode(s: &str) -> Option<Vec<u8>> {
    if s.contains('=') { return None; }
    let mut bits: u32 = 0;
    let mut bit_count = 0;
    let mut out = Vec::new();
    for c in s.bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'-' => 62,
            b'_' => 63,
            _ => return None,
        } as u32;
        bits = (bits << 6) | v;
        bit_count += 6;
        while bit_count >= 8 {
            bit_count -= 8;
            out.push(((bits >> bit_count) & 0xff) as u8);
        }
    }
    if bit_count > 0 && (bits & ((1 << bit_count) - 1)) != 0 { return None; }
    Some(out)
}

fn b64url_encode(bytes: &[u8]) -> String {
    const ALPH: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::new();
    let mut i = 0;
    while i + 3 <= bytes.len() {
        let n = ((bytes[i] as u32) << 16) | ((bytes[i+1] as u32) << 8) | bytes[i+2] as u32;
        out.push(ALPH[((n >> 18) & 63) as usize] as char);
        out.push(ALPH[((n >> 12) & 63) as usize] as char);
        out.push(ALPH[((n >> 6) & 63) as usize] as char);
        out.push(ALPH[(n & 63) as usize] as char);
        i += 3;
    }
    let rem = bytes.len() - i;
    if rem == 1 {
        let n = (bytes[i] as u32) << 16;
        out.push(ALPH[((n >> 18) & 63) as usize] as char);
        out.push(ALPH[((n >> 12) & 63) as usize] as char);
    } else if rem == 2 {
        let n = ((bytes[i] as u32) << 16) | ((bytes[i+1] as u32) << 8);
        out.push(ALPH[((n >> 18) & 63) as usize] as char);
        out.push(ALPH[((n >> 12) & 63) as usize] as char);
        out.push(ALPH[((n >> 6) & 63) as usize] as char);
    }
    out
}

fn dedup_in_order(items: &mut Vec<String>) {
    let mut seen = HashSet::new();
    items.retain(|x| seen.insert(x.clone()));
}

fn json_string(s: &str) -> String {
    let mut out = String::from("\"");
    for ch in s.chars() {
        match ch { '\\' => out.push_str("\\\\"), '"' => out.push_str("\\\""), '\n' => out.push_str("\\n"), '\r' => out.push_str("\\r"), '\t' => out.push_str("\\t"), c => out.push(c) }
    }
    out.push('"'); out
}

fn string_array<I: IntoIterator<Item=String>>(iter: I) -> String {
    let parts: Vec<String> = iter.into_iter().map(|s| json_string(&s)).collect();
    format!("[{}]", parts.join(","))
}

fn render_report(accepted: usize, rejected: usize, decisions: &[Decision], nonces: &BTreeSet<String>, orders: &BTreeSet<String>) -> String {
    let mut s = String::new();
    s.push_str("{\n");
    s.push_str(&format!("  \"accepted\": {},\n", if rejected == 0 { "true" } else { "false" }));
    s.push_str("  \"records\": [\n");
    for (i, d) in decisions.iter().enumerate() {
        s.push_str("    {");
        s.push_str(&format!("\"account\":{},\"decision\":{},\"line\":{},\"nonce\":{},\"order\":{},\"reasons\":{}", json_string(&d.account), json_string(&d.decision), d.line, json_string(&d.nonce), json_string(&d.order), string_array(d.reasons.clone())));
        s.push_str(if i + 1 == decisions.len() { "}\n" } else { "},\n" });
    }
    s.push_str("  ],\n");
    s.push_str(&format!("  \"state\": {{\"new_nonces\":{},\"ready_orders\":{}}},\n", string_array(nonces.iter().cloned()), string_array(orders.iter().cloned())));
    s.push_str(&format!("  \"summary\": {{\"accepted\":{},\"rejected\":{}}}\n", accepted, rejected));
    s.push_str("}\n");
    s
}
RS

/app/acmegate/scripts/build.sh
