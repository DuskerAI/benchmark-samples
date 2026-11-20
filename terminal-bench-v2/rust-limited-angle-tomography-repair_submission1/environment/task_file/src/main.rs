use std::env;

fn main() {
    if let Err(err) = tomo_repair::run_from_args(env::args().skip(1).collect()) {
        eprintln!("{err}");
        std::process::exit(1);
    }
}
