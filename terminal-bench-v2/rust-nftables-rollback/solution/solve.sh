#!/bin/bash
set -euo pipefail

cat > /app/firewall/src/main.rs <<'RS'
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::path::PathBuf;

type TableKey = (String, String);
type SetKey = (String, String, String);
type ChainKey = (String, String, String);
type RuleKey = (String, String, String, u64);

#[derive(Clone, Debug)]
struct Ruleset {
    tables: BTreeSet<TableKey>,
    sets: BTreeMap<SetKey, Vec<String>>,
    chains: BTreeMap<ChainKey, (String, i64)>,
    rules: BTreeMap<RuleKey, Vec<String>>,
}

#[derive(Clone, Debug)]
enum Op {
    AddTable(String, String),
    DeleteTable(String, String),
    AddSet(String, String, String, Vec<String>),
    DeleteSet(String, String, String),
    ReplaceSet(String, String, String, Vec<String>),
    AddChain(String, String, String, String, i64),
    DeleteChain(String, String, String),
    RenameChain(String, String, String, String),
    AddRule(String, String, String, u64, Vec<String>),
    DeleteRule(String, String, String, u64),
    ReplaceRule(String, String, String, u64, Vec<String>),
    MoveRule(String, String, String, u64, u64),
}

fn arg_value(args: &[String], name: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == name).map(|w| w[1].clone())
}

fn clean_lines(text: &str) -> Vec<String> {
    text.lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .map(str::to_string)
        .collect()
}

fn elements(token: &str) -> Vec<String> {
    if token == "-" {
        return Vec::new();
    }
    token
        .split(',')
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .map(str::to_string)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn expression(token: &str) -> Vec<String> {
    if token == "-" {
        return Vec::new();
    }
    token
        .split(';')
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .map(str::to_string)
        .collect()
}

fn fmt_elements(values: &[String]) -> String {
    if values.is_empty() {
        "-".to_string()
    } else {
        values.join(",")
    }
}

fn fmt_expr(values: &[String]) -> String {
    if values.is_empty() {
        "-".to_string()
    } else {
        values.join(";")
    }
}

fn validate_expr(rs: &Ruleset, family: &str, table: &str, atoms: &[String]) -> Result<(), String> {
    for atom in atoms {
        if let Some(name) = atom.strip_prefix("set:") {
            if !rs.sets.contains_key(&(family.to_string(), table.to_string(), name.to_string())) {
                return Err("missing-reference-set".to_string());
            }
        } else if let Some(name) = atom.strip_prefix("jump:") {
            if !rs.chains.contains_key(&(family.to_string(), table.to_string(), name.to_string())) {
                return Err("missing-jump".to_string());
            }
        } else if let Some(verdict) = atom.strip_prefix("verdict:") {
            if !matches!(verdict, "accept" | "drop" | "reject" | "return") {
                return Err("bad-verdict".to_string());
            }
        }
    }
    Ok(())
}

fn parse_base(text: &str) -> Result<Ruleset, String> {
    let mut rs = Ruleset {
        tables: BTreeSet::new(),
        sets: BTreeMap::new(),
        chains: BTreeMap::new(),
        rules: BTreeMap::new(),
    };
    for line in clean_lines(text) {
        let parts: Vec<&str> = line.split_whitespace().collect();
        match parts.as_slice() {
            ["table", family, table] => {
                if !rs.tables.insert((family.to_string(), table.to_string())) {
                    return Err("duplicate-table".to_string());
                }
            }
            ["set", family, table, name, elems] => {
                if !rs.tables.contains(&(family.to_string(), table.to_string())) {
                    return Err("missing-table".to_string());
                }
                let key = (family.to_string(), table.to_string(), name.to_string());
                if rs.sets.insert(key, elements(elems)).is_some() {
                    return Err("duplicate-set".to_string());
                }
            }
            ["chain", family, table, name, hook, priority] => {
                if !rs.tables.contains(&(family.to_string(), table.to_string())) {
                    return Err("missing-table".to_string());
                }
                let prio = priority.parse::<i64>().map_err(|_| "bad-priority".to_string())?;
                if *hook != "-" {
                    for ((f, t, _), (h, p)) in &rs.chains {
                        if f == family && t == table && h != "-" && h == hook && *p == prio {
                            return Err("hook-collision".to_string());
                        }
                    }
                }
                let key = (family.to_string(), table.to_string(), name.to_string());
                if rs.chains.insert(key, (hook.to_string(), prio)).is_some() {
                    return Err("duplicate-chain".to_string());
                }
            }
            ["rule", family, table, chain, handle, expr] => {
                let h = handle.parse::<u64>().map_err(|_| "bad-handle".to_string())?;
                if !rs.chains.contains_key(&(family.to_string(), table.to_string(), chain.to_string())) {
                    return Err("missing-chain".to_string());
                }
                let atoms = expression(expr);
                validate_expr(&rs, family, table, &atoms)?;
                let key = (family.to_string(), table.to_string(), chain.to_string(), h);
                if rs.rules.insert(key, atoms).is_some() {
                    return Err("duplicate-rule".to_string());
                }
            }
            _ => return Err("syntax".to_string()),
        }
    }
    Ok(rs)
}

fn parse_op(line: &str) -> Result<Op, String> {
    let parts: Vec<&str> = line.split_whitespace().collect();
    match parts.as_slice() {
        ["add", "table", family, table] => Ok(Op::AddTable(family.to_string(), table.to_string())),
        ["delete", "table", family, table] => Ok(Op::DeleteTable(family.to_string(), table.to_string())),
        ["add", "set", family, table, name, elems] => Ok(Op::AddSet(family.to_string(), table.to_string(), name.to_string(), elements(elems))),
        ["delete", "set", family, table, name] => Ok(Op::DeleteSet(family.to_string(), table.to_string(), name.to_string())),
        ["replace", "set", family, table, name, elems] => Ok(Op::ReplaceSet(family.to_string(), table.to_string(), name.to_string(), elements(elems))),
        ["add", "chain", family, table, name, hook, priority] => {
            let prio = priority.parse::<i64>().map_err(|_| "bad-priority".to_string())?;
            Ok(Op::AddChain(family.to_string(), table.to_string(), name.to_string(), hook.to_string(), prio))
        }
        ["delete", "chain", family, table, name] => Ok(Op::DeleteChain(family.to_string(), table.to_string(), name.to_string())),
        ["rename", "chain", family, table, old, new] => Ok(Op::RenameChain(family.to_string(), table.to_string(), old.to_string(), new.to_string())),
        ["add", "rule", family, table, chain, handle, expr] => {
            let h = handle.parse::<u64>().map_err(|_| "bad-handle".to_string())?;
            Ok(Op::AddRule(family.to_string(), table.to_string(), chain.to_string(), h, expression(expr)))
        }
        ["replace", "rule", family, table, chain, handle, expr] => {
            let h = handle.parse::<u64>().map_err(|_| "bad-handle".to_string())?;
            Ok(Op::ReplaceRule(family.to_string(), table.to_string(), chain.to_string(), h, expression(expr)))
        }
        ["delete", "rule", family, table, chain, handle] => {
            let h = handle.parse::<u64>().map_err(|_| "bad-handle".to_string())?;
            Ok(Op::DeleteRule(family.to_string(), table.to_string(), chain.to_string(), h))
        }
        ["move", "rule", family, table, chain, old_handle, new_handle] => {
            let old = old_handle.parse::<u64>().map_err(|_| "bad-handle".to_string())?;
            let new = new_handle.parse::<u64>().map_err(|_| "bad-handle".to_string())?;
            Ok(Op::MoveRule(family.to_string(), table.to_string(), chain.to_string(), old, new))
        }
        _ => Err("syntax".to_string()),
    }
}

fn canonical_op(op: &Op) -> String {
    match op {
        Op::AddTable(f, t) => format!("add table {f} {t}"),
        Op::DeleteTable(f, t) => format!("delete table {f} {t}"),
        Op::AddSet(f, t, n, e) => format!("add set {f} {t} {n} {}", fmt_elements(e)),
        Op::DeleteSet(f, t, n) => format!("delete set {f} {t} {n}"),
        Op::ReplaceSet(f, t, n, e) => format!("replace set {f} {t} {n} {}", fmt_elements(e)),
        Op::AddChain(f, t, n, h, p) => format!("add chain {f} {t} {n} {h} {p}"),
        Op::DeleteChain(f, t, n) => format!("delete chain {f} {t} {n}"),
        Op::RenameChain(f, t, old, new) => format!("rename chain {f} {t} {old} {new}"),
        Op::AddRule(f, t, c, h, e) => format!("add rule {f} {t} {c} {h} {}", fmt_expr(e)),
        Op::DeleteRule(f, t, c, h) => format!("delete rule {f} {t} {c} {h}"),
        Op::ReplaceRule(f, t, c, h, e) => format!("replace rule {f} {t} {c} {h} {}", fmt_expr(e)),
        Op::MoveRule(f, t, c, old, new) => format!("move rule {f} {t} {c} {old} {new}"),
    }
}

fn apply_op(rs: &mut Ruleset, op: &Op) -> Result<String, String> {
    match op {
        Op::AddTable(f, t) => {
            let key = (f.clone(), t.clone());
            if rs.tables.contains(&key) {
                return Err("duplicate-table".to_string());
            }
            rs.tables.insert(key);
            Ok(format!("delete table {f} {t}"))
        }
        Op::DeleteTable(f, t) => {
            let key = (f.clone(), t.clone());
            if !rs.tables.contains(&key) {
                return Err("missing-table".to_string());
            }
            if rs.sets.keys().any(|(sf, st, _)| sf == f && st == t)
                || rs.chains.keys().any(|(cf, ct, _)| cf == f && ct == t)
                || rs.rules.keys().any(|(rf, rt, _, _)| rf == f && rt == t)
            {
                return Err("table-not-empty".to_string());
            }
            rs.tables.remove(&key);
            Ok(format!("add table {f} {t}"))
        }
        Op::AddSet(f, t, n, e) => {
            if !rs.tables.contains(&(f.clone(), t.clone())) {
                return Err("missing-table".to_string());
            }
            let key = (f.clone(), t.clone(), n.clone());
            if rs.sets.contains_key(&key) {
                return Err("duplicate-set".to_string());
            }
            rs.sets.insert(key, e.clone());
            Ok(format!("delete set {f} {t} {n}"))
        }
        Op::DeleteSet(f, t, n) => {
            let key = (f.clone(), t.clone(), n.clone());
            let old = match rs.sets.get(&key) {
                Some(v) => v.clone(),
                None => return Err("missing-set".to_string()),
            };
            let needle = format!("set:{n}");
            if rs.rules.iter().any(|((rf, rt, _, _), atoms)| rf == f && rt == t && atoms.iter().any(|a| a == &needle)) {
                return Err("set-in-use".to_string());
            }
            rs.sets.remove(&key);
            Ok(format!("add set {f} {t} {n} {}", fmt_elements(&old)))
        }
        Op::ReplaceSet(f, t, n, e) => {
            let key = (f.clone(), t.clone(), n.clone());
            let old = match rs.sets.get(&key) {
                Some(v) => v.clone(),
                None => return Err("missing-set".to_string()),
            };
            rs.sets.insert(key, e.clone());
            Ok(format!("replace set {f} {t} {n} {}", fmt_elements(&old)))
        }
        Op::AddChain(f, t, n, hook, prio) => {
            if !rs.tables.contains(&(f.clone(), t.clone())) {
                return Err("missing-table".to_string());
            }
            let key = (f.clone(), t.clone(), n.clone());
            if rs.chains.contains_key(&key) {
                return Err("duplicate-chain".to_string());
            }
            if hook != "-" {
                for ((cf, ct, _), (existing_hook, existing_prio)) in &rs.chains {
                    if cf == f && ct == t && existing_hook != "-" && existing_hook == hook && existing_prio == prio {
                        return Err("hook-collision".to_string());
                    }
                }
            }
            rs.chains.insert(key, (hook.clone(), *prio));
            Ok(format!("delete chain {f} {t} {n}"))
        }
        Op::DeleteChain(f, t, n) => {
            let key = (f.clone(), t.clone(), n.clone());
            let old = match rs.chains.get(&key) {
                Some(v) => v.clone(),
                None => return Err("missing-chain".to_string()),
            };
            if rs.rules.keys().any(|(rf, rt, rc, _)| rf == f && rt == t && rc == n) {
                return Err("chain-not-empty".to_string());
            }
            let needle = format!("jump:{n}");
            if rs.rules.iter().any(|((rf, rt, _, _), atoms)| rf == f && rt == t && atoms.iter().any(|a| a == &needle)) {
                return Err("chain-referenced".to_string());
            }
            rs.chains.remove(&key);
            Ok(format!("add chain {f} {t} {n} {} {}", old.0, old.1))
        }
        Op::RenameChain(f, t, old, new) => {
            let old_key = (f.clone(), t.clone(), old.clone());
            let new_key = (f.clone(), t.clone(), new.clone());
            let chain = match rs.chains.get(&old_key) {
                Some(v) => v.clone(),
                None => return Err("missing-chain".to_string()),
            };
            if rs.chains.contains_key(&new_key) {
                return Err("duplicate-chain".to_string());
            }
            rs.chains.remove(&old_key);
            rs.chains.insert(new_key.clone(), chain);
            let mut moved = Vec::new();
            for ((rf, rt, rc, rh), atoms) in &rs.rules {
                if rf == f && rt == t && rc == old {
                    moved.push(((rf.clone(), rt.clone(), rc.clone(), *rh), atoms.clone()));
                }
            }
            for ((rf, rt, rc, rh), atoms) in moved {
                rs.rules.remove(&(rf.clone(), rt.clone(), rc, rh));
                rs.rules.insert((rf, rt, new.clone(), rh), atoms);
            }
            let needle = format!("jump:{old}");
            let replacement = format!("jump:{new}");
            for ((rf, rt, _, _), atoms) in rs.rules.iter_mut() {
                if rf == f && rt == t {
                    for atom in atoms.iter_mut() {
                        if atom == &needle {
                            *atom = replacement.clone();
                        }
                    }
                }
            }
            Ok(format!("rename chain {f} {t} {new} {old}"))
        }
        Op::AddRule(f, t, c, h, e) => {
            if !rs.chains.contains_key(&(f.clone(), t.clone(), c.clone())) {
                return Err("missing-chain".to_string());
            }
            let key = (f.clone(), t.clone(), c.clone(), *h);
            if rs.rules.contains_key(&key) {
                return Err("duplicate-rule".to_string());
            }
            validate_expr(rs, f, t, e)?;
            rs.rules.insert(key, e.clone());
            Ok(format!("delete rule {f} {t} {c} {h}"))
        }
        Op::DeleteRule(f, t, c, h) => {
            let key = (f.clone(), t.clone(), c.clone(), *h);
            let old = match rs.rules.get(&key) {
                Some(v) => v.clone(),
                None => return Err("missing-rule".to_string()),
            };
            rs.rules.remove(&key);
            Ok(format!("add rule {f} {t} {c} {h} {}", fmt_expr(&old)))
        }
        Op::ReplaceRule(f, t, c, h, e) => {
            let key = (f.clone(), t.clone(), c.clone(), *h);
            let old = match rs.rules.get(&key) {
                Some(v) => v.clone(),
                None => return Err("missing-rule".to_string()),
            };
            validate_expr(rs, f, t, e)?;
            rs.rules.insert(key, e.clone());
            Ok(format!("replace rule {f} {t} {c} {h} {}", fmt_expr(&old)))
        }
        Op::MoveRule(f, t, c, old, new) => {
            let old_key = (f.clone(), t.clone(), c.clone(), *old);
            let new_key = (f.clone(), t.clone(), c.clone(), *new);
            let atoms = match rs.rules.get(&old_key) {
                Some(v) => v.clone(),
                None => return Err("missing-rule".to_string()),
            };
            if rs.rules.contains_key(&new_key) {
                return Err("duplicate-rule".to_string());
            }
            rs.rules.remove(&old_key);
            rs.rules.insert(new_key, atoms);
            Ok(format!("move rule {f} {t} {c} {new} {old}"))
        }
    }
}

fn canonical_ruleset(rs: &Ruleset) -> String {
    let mut out = Vec::new();
    for (family, table) in &rs.tables {
        out.push(format!("table {family} {table}"));
    }
    for ((family, table, name), elems) in &rs.sets {
        out.push(format!("set {family} {table} {name} {}", fmt_elements(elems)));
    }
    for ((family, table, name), (hook, prio)) in &rs.chains {
        out.push(format!("chain {family} {table} {name} {hook} {prio}"));
    }
    for ((family, table, chain, handle), expr) in &rs.rules {
        out.push(format!("rule {family} {table} {chain} {handle} {}", fmt_expr(expr)));
    }
    if out.is_empty() {
        String::new()
    } else {
        format!("{}\n", out.join("\n"))
    }
}

fn parse_state(text: &str, plan_len: usize) -> Result<(bool, usize), String> {
    let lines = clean_lines(text);
    if lines.is_empty() || lines[0] == "clean" {
        return Ok((false, 0));
    }
    if lines[0] != "applying" {
        return Err("state-prefix".to_string());
    }
    for (i, line) in lines.iter().skip(1).enumerate() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() != 2 || parts[0] != "applied" {
            return Err("state-prefix".to_string());
        }
        let num = parts[1].parse::<usize>().map_err(|_| "state-prefix".to_string())?;
        if num != i + 1 || num > plan_len {
            return Err("state-prefix".to_string());
        }
    }
    Ok((true, lines.len() - 1))
}

fn write_outputs(out_dir: &str, approved: &[String], report: &[String], final_ruleset: &str) -> Result<(), String> {
    let out = PathBuf::from(out_dir);
    fs::create_dir_all(&out).map_err(|e| e.to_string())?;
    for entry in fs::read_dir(&out).map_err(|e| e.to_string())? {
        let path = entry.map_err(|e| e.to_string())?.path();
        if path.is_dir() {
            fs::remove_dir_all(path).map_err(|e| e.to_string())?;
        } else {
            fs::remove_file(path).map_err(|e| e.to_string())?;
        }
    }
    let approved_text = if approved.is_empty() { String::new() } else { format!("{}\n", approved.join("\n")) };
    fs::write(out.join("approved.ops"), approved_text).map_err(|e| e.to_string())?;
    fs::write(out.join("report.tsv"), format!("{}\n", report.join("\n"))).map_err(|e| e.to_string())?;
    fs::write(out.join("final.ruleset"), final_ruleset).map_err(|e| e.to_string())?;
    fs::write(out.join("state.out"), "clean\n").map_err(|e| e.to_string())?;
    Ok(())
}

fn run() -> Result<(), String> {
    let args: Vec<String> = env::args().collect();
    let base_path = arg_value(&args, "--base").ok_or_else(|| "missing-base".to_string())?;
    let plan_path = arg_value(&args, "--plan").ok_or_else(|| "missing-plan".to_string())?;
    let state_path = arg_value(&args, "--state").ok_or_else(|| "missing-state".to_string())?;
    let out_dir = arg_value(&args, "--out").ok_or_else(|| "missing-out".to_string())?;

    let base_text = fs::read_to_string(base_path).map_err(|e| e.to_string())?;
    let plan_text = fs::read_to_string(plan_path).map_err(|e| e.to_string())?;
    let state_text = fs::read_to_string(state_path).map_err(|e| e.to_string())?;
    let base = parse_base(&base_text)?;
    let plan_lines = clean_lines(&plan_text);

    let mut work = base.clone();
    let mut forward = Vec::new();
    let mut inverses = Vec::new();
    let mut reject: Option<(usize, String)> = None;
    for (idx, line) in plan_lines.iter().enumerate() {
        let op = match parse_op(line) {
            Ok(op) => op,
            Err(reason) => {
                reject = Some((idx + 1, reason));
                break;
            }
        };
        let canonical = canonical_op(&op);
        match apply_op(&mut work, &op) {
            Ok(inverse) => {
                forward.push(canonical);
                inverses.push(inverse);
            }
            Err(reason) => {
                reject = Some((idx + 1, reason));
                break;
            }
        }
    }

    let (applying, prefix) = match parse_state(&state_text, plan_lines.len()) {
        Ok(v) => v,
        Err(reason) => {
            reject.get_or_insert((1, reason));
            (false, 0)
        }
    };

    let mut report = vec!["op\tdecision\treason".to_string()];
    let mut approved = Vec::new();
    let final_text;
    if let Some((bad_idx, reason)) = reject {
        for idx in 1..=plan_lines.len() {
            if applying && idx <= prefix {
                report.push(format!("{idx}\tROLLBACK\trecovery"));
            } else if idx < bad_idx {
                report.push(format!("{idx}\tSKIP\tatomic-rollback"));
            } else if idx == bad_idx {
                report.push(format!("{idx}\tREJECT\t{reason}"));
            } else {
                report.push(format!("{idx}\tSKIP\tblocked-by-reject"));
            }
        }
        for idx in (1..=prefix).rev() {
            if let Some(inverse) = inverses.get(idx - 1) {
                approved.push(format!("{idx:04}\t{inverse}"));
            }
        }
        final_text = canonical_ruleset(&base);
    } else {
        for idx in 1..=plan_lines.len() {
            if applying && idx <= prefix {
                report.push(format!("{idx}\tAPPROVE\talready-applied"));
            } else {
                report.push(format!("{idx}\tAPPROVE\tapplied"));
                approved.push(format!("{idx:04}\t{}", forward[idx - 1]));
            }
        }
        final_text = canonical_ruleset(&work);
    }
    write_outputs(&out_dir, &approved, &report, &final_text)
}

fn main() {
    if let Err(err) = run() {
        eprintln!("fwplan: {err}");
        std::process::exit(2);
    }
}
RS

/app/firewall/scripts/build.sh
