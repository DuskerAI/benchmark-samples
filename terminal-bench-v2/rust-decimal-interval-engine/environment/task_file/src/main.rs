mod decimal;
mod engine;
mod json;
mod parser;

use std::env;
use std::fs;
use std::process;

fn run() -> Result<String, String> {
    let mut args = env::args().skip(1);
    match (args.next().as_deref(), args.next(), args.next()) {
        (Some("evaluate"), Some(path), None) => {
            let text = fs::read_to_string(&path).map_err(|e| format!("{path}: {e}"))?;
            let portfolio = parser::parse_portfolio(&text)?;
            let report = engine::evaluate(&portfolio)?;
            Ok(json::render(&report))
        }
        _ => Err("usage: riskcalc evaluate <portfolio-file>".to_string()),
    }
}

fn main() {
    match run() {
        Ok(out) => println!("{out}"),
        Err(err) => {
            eprintln!("error: {err}");
            process::exit(1);
        }
    }
}
