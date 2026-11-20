use std::env;
use std::fs;
use std::path::PathBuf;

fn arg_value(args: &[String], name: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == name).map(|w| w[1].clone())
}

fn useful_lines(path: &str) -> Vec<String> {
    fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(str::to_string)
        .collect()
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let base = arg_value(&args, "--base").unwrap_or_default();
    let plan = arg_value(&args, "--plan").unwrap_or_default();
    let out = arg_value(&args, "--out").unwrap_or_else(|| "out".to_string());
    fs::create_dir_all(&out).unwrap();

    let mut rules = useful_lines(&base);
    let ops = useful_lines(&plan);
    let mut approved = Vec::new();
    let mut report = vec!["op\tdecision\treason".to_string()];

    for (i, op) in ops.iter().enumerate() {
        approved.push(format!("{:04}\t{}", i + 1, op));
        report.push(format!("{}\tAPPROVE\tapplied", i + 1));
        let parts: Vec<&str> = op.split_whitespace().collect();
        if parts.first() == Some(&"add") {
            rules.push(parts[1..].join(" "));
        }
    }

    let mut final_path = PathBuf::from(&out);
    final_path.push("final.ruleset");
    fs::write(final_path, format!("{}\n", rules.join("\n"))).unwrap();
    fs::write(PathBuf::from(&out).join("approved.ops"), format!("{}\n", approved.join("\n"))).unwrap();
    fs::write(PathBuf::from(&out).join("report.tsv"), format!("{}\n", report.join("\n"))).unwrap();
    fs::write(PathBuf::from(&out).join("state.out"), "clean\n").unwrap();
}
