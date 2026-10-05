use anyhow::{Context, Result, ensure};
use firehol_mmdb::{Options, generate};
use std::{env, path::Path};

fn main() -> Result<()> {
    let args: Vec<_> = env::args().collect();
    ensure!(
        args.len() == 3,
        "Usage: firehol-mmdb <blocklist-directory> <output.mmdb>"
    );
    let mut options = Options::default();
    if let Ok(value) = env::var("FIREHOL_PARSER_THREADS") {
        options.threads = value
            .parse()
            .context("FIREHOL_PARSER_THREADS must be a positive integer")?;
    }
    generate(Path::new(&args[1]), Path::new(&args[2]), &options)?;
    Ok(())
}
