use std::env;
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 {
        eprintln!(
            "usage: {} <observation_dir> <plate_solutions_csv> <calibrated_sources_csv>",
            args.first().map(String::as_str).unwrap_or("astrofit")
        );
        std::process::exit(2);
    }

    let obs_dir = PathBuf::from(&args[1]);
    let plate_out = PathBuf::from(&args[2]);
    let calibrated_out = PathBuf::from(&args[3]);

    if let Err(err) = astrofit::run(&obs_dir, &plate_out, &calibrated_out) {
        eprintln!("astrofit failed: {err}");
        std::process::exit(1);
    }
}
