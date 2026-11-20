use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::env;
use std::fs;
use std::path::Path;

struct Account { key: String, active: bool }
struct Evidence { line: usize, order: String, account: String, nonce: String, _challenge: String, protected: String, payload: String, signature: String }

fn main() {
    if let Err(e) = run() {
        eprintln!("{}", e);
        std::process::exit(2);
    }
}

fn run() -> Result<(), String> {
    let args: Vec<String> = env::args().collect();
    let mut flags = BTreeMap::new();
    let mut i = 1;
    while i + 1 < args.len() {
        flags.insert(args[i].clone(), args[i + 1].clone());
        i += 2;
    }
    let trust_path = flags.get("--trust").ok_or("missing --trust")?;
    let bundle_path = flags.get("--bundle").ok_or("missing --bundle")?;
    let out_path = flags.get("--out").ok_or("missing --out")?;
    let _state_path = flags.get("--state").ok_or("missing --state")?;
    let _now = flags.get("--now").ok_or("missing --now")?;
    let accounts = read_trust(trust_path)?;
    let evidence = read_evidence(bundle_path)?;

    let mut decisions = Vec::new();
    let mut nonces = BTreeSet::new();
    let mut orders = BTreeSet::new();
    for rec in evidence {
        let mut reasons = Vec::new();
        match accounts.get(&rec.account) {
            Some(acct) if acct.active => {
                let sig = weak_mac(&acct.key, &rec.protected, &rec.payload);
                if sig != rec.signature { reasons.push("BAD_SIGNATURE".to_string()); }
            }
            _ => reasons.push("BAD_ACCOUNT".to_string()),
        }
        let decision = if reasons.is_empty() { "ACCEPT" } else { "REJECT" };
        if decision == "ACCEPT" {
            nonces.insert(rec.nonce.clone());
            orders.insert(rec.order.clone());
        }
        decisions.push((rec.line, rec.order, rec.account, rec.nonce, decision.to_string(), reasons));
    }
    let accepted = decisions.iter().filter(|d| d.4 == "ACCEPT").count();
    let rejected = decisions.len() - accepted;
    if let Some(parent) = Path::new(out_path).parent() { if !parent.as_os_str().is_empty() { fs::create_dir_all(parent).map_err(|e| e.to_string())?; } }
    fs::write(out_path, render_report(accepted, rejected, &decisions, &nonces, &orders)).map_err(|e| e.to_string())?;
    Ok(())
}

fn read_trust(path: &str) -> Result<BTreeMap<String, Account>, String> {
    let mut out = BTreeMap::new();
    for raw in fs::read_to_string(path).map_err(|e| e.to_string())?.lines() {
        let line = raw.trim_end();
        if line.is_empty() || line.starts_with('#') { continue; }
        let parts: Vec<&str> = line.split('\t').collect();
        if parts.len() == 3 { out.insert(parts[0].to_string(), Account { key: parts[1].to_string(), active: parts[2] != "revoked" }); }
    }
    Ok(out)
}

fn read_evidence(dir: &str) -> Result<Vec<Evidence>, String> {
    let mut out = Vec::new();
    for (idx, raw) in fs::read_to_string(Path::new(dir).join("evidence.tsv")).map_err(|e| e.to_string())?.lines().enumerate() {
        let line = raw.trim_end();
        if line.is_empty() || line.starts_with('#') { continue; }
        let parts: Vec<&str> = line.split('\t').collect();
        if parts.len() == 7 {
            out.push(Evidence { line: idx + 1, order: parts[0].to_string(), account: parts[1].to_string(), nonce: parts[2].to_string(), _challenge: parts[3].to_string(), protected: parts[4].to_string(), payload: parts[5].to_string(), signature: parts[6].to_string() });
        }
    }
    Ok(out)
}

fn weak_mac(key: &str, protected: &str, payload: &str) -> String {
    let mut h: u64 = 0;
    for b in key.bytes().chain(protected.bytes()).chain(payload.bytes()) { h = h.wrapping_add(b as u64); }
    format!("{:016x}", h)
}

fn json_string(s: &str) -> String { format!("\"{}\"", s.replace('"', "\\\"")) }
fn array(vals: impl Iterator<Item=String>) -> String { format!("[{}]", vals.map(|v| json_string(&v)).collect::<Vec<_>>().join(",")) }

fn render_report(accepted: usize, rejected: usize, decisions: &[(usize, String, String, String, String, Vec<String>)], nonces: &BTreeSet<String>, orders: &BTreeSet<String>) -> String {
    let mut s = String::from("{\n");
    s.push_str(&format!("  \"accepted\": {},\n", if rejected == 0 { "true" } else { "false" }));
    s.push_str("  \"records\": [\n");
    for (i, d) in decisions.iter().enumerate() {
        s.push_str(&format!("    {{\"account\":{},\"decision\":{},\"line\":{},\"nonce\":{},\"order\":{},\"reasons\":{}}}{}\n", json_string(&d.2), json_string(&d.4), d.0, json_string(&d.3), json_string(&d.1), array(d.5.clone().into_iter()), if i + 1 == decisions.len() { "" } else { "," }));
    }
    s.push_str("  ],\n");
    s.push_str(&format!("  \"state\": {{\"new_nonces\":{},\"ready_orders\":{}}},\n", array(nonces.iter().cloned()), array(orders.iter().cloned())));
    s.push_str(&format!("  \"summary\": {{\"accepted\":{},\"rejected\":{}}}\n}}\n", accepted, rejected));
    s
}
