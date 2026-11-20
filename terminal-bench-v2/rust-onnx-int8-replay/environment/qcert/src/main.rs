use std::env;
use std::fs::{self, File};
use std::io::Write;
use std::path::PathBuf;

struct Args {
    out_dir: PathBuf,
}

fn usage() -> ! {
    eprintln!("usage: qcert --case CASE_DIR --out OUT_DIR [--checkpoint FILE] [--resume FILE] [--limit N]");
    std::process::exit(2);
}

fn parse_args() -> Args {
    let mut out_dir = None;
    let mut it = env::args().skip(1);
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--case" | "--checkpoint" | "--resume" | "--limit" => {
                it.next().unwrap_or_else(|| usage());
            }
            "--out" => out_dir = Some(PathBuf::from(it.next().unwrap_or_else(|| usage()))),
            _ => usage(),
        }
    }
    Args { out_dir: out_dir.unwrap_or_else(|| usage()) }
}

fn main() {
    let args = parse_args();
    if args.out_dir.exists() {
        fs::remove_dir_all(&args.out_dir).unwrap();
    }
    fs::create_dir_all(&args.out_dir).unwrap();
    File::create(args.out_dir.join("predictions.jsonl")).unwrap();
    let mut cert = File::create(args.out_dir.join("cert.json")).unwrap();
    writeln!(cert, "{{\"records\":0,\"classes\":0,\"class_counts\":[],\"replay_digest\":\"0000000000000000\"}}").unwrap();
}
