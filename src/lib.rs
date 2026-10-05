use anyhow::{Context, Result, bail, ensure};
use libmaxminddb_rs::{IpNetwork, MergeStrategy, MetadataBuilder, Reader, Value, Writer};
use rayon::prelude::*;
use std::{
    cmp::Reverse,
    collections::{BTreeMap, BinaryHeap},
    fs::{self, File},
    io::{BufRead, BufReader, BufWriter, Read, Write},
    net::{IpAddr, Ipv4Addr, Ipv6Addr},
    path::{Path, PathBuf},
    process::Command,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
        mpsc::{SyncSender, sync_channel},
    },
};

pub const FIELDS: [&str; 7] = [
    "files",
    "categories",
    "maintainers",
    "maintainer_urls",
    "source_urls",
    "source_file_dates",
    "versions",
];
const BATCH_SIZE: usize = 32_768;
const QUEUE_SIZE: usize = 8;
const FAN_IN: usize = 48;

#[derive(Clone, Debug)]
pub struct Options {
    pub threads: usize,
    pub batch_size: usize,
    pub queue_size: usize,
    /// Source commit timestamp; omitted values are read from the source Git checkout.
    pub build_epoch: Option<u64>,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            threads: std::thread::available_parallelism().map_or(1, usize::from),
            batch_size: BATCH_SIZE,
            queue_size: QUEUE_SIZE,
            build_epoch: None,
        }
    }
}

#[derive(Debug)]
pub struct Stats {
    pub files: usize,
    pub entries: u64,
    pub networks: u64,
    pub bytes: u64,
}

/// Original contributions are never deduplicated. Sorting puts parents before children.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
struct Entry {
    family: u8,
    address: u128,
    prefix: u8,
    source: u64,
}
impl Entry {
    fn new(network: IpNetwork, source: u64) -> Self {
        match network.trunc() {
            IpNetwork::V4(n) => Self {
                family: 1,
                address: u32::from(n.network()) as u128,
                prefix: n.prefix_len(),
                source,
            },
            IpNetwork::V6(n) => Self {
                family: 0,
                address: u128::from(n.network()),
                prefix: n.prefix_len(),
                source,
            },
        }
    }
    fn network(self) -> Result<IpNetwork> {
        let ip = if self.family == 1 {
            IpAddr::V4(Ipv4Addr::from(self.address as u32))
        } else {
            IpAddr::V6(Ipv6Addr::from(self.address))
        };
        Ok(IpNetwork::new(ip, self.prefix)?)
    }
    fn same_network(self, other: Self) -> bool {
        (self.family, self.address, self.prefix) == (other.family, other.address, other.prefix)
    }
    fn contains(self, other: Self) -> bool {
        if self.family != other.family || self.prefix > other.prefix {
            return false;
        }
        let width = if self.family == 1 { 32 } else { 128 };
        let mask = if self.prefix == 0 {
            0
        } else {
            u128::MAX << (width - self.prefix)
        };
        self.address & mask == other.address & mask
    }
    fn write(self, output: &mut impl Write) -> Result<()> {
        output.write_all(&[self.family, self.prefix])?;
        output.write_all(&self.address.to_be_bytes())?;
        output.write_all(&self.source.to_be_bytes())?;
        Ok(())
    }
    fn read(input: &mut impl Read) -> Result<Option<Self>> {
        let mut first = [0u8; 1];
        if input.read(&mut first)? == 0 {
            return Ok(None);
        }
        let mut rest = [0u8; 25];
        input
            .read_exact(&mut rest)
            .context("Truncated temporary sort run; rerun the build with sufficient disk space")?;
        Ok(Some(Self {
            family: first[0],
            prefix: rest[0],
            address: u128::from_be_bytes(rest[1..17].try_into()?),
            source: u64::from_be_bytes(rest[17..25].try_into()?),
        }))
    }
}

struct Batch {
    entries: Vec<Entry>,
    source: u64,
    metadata: Arc<Value>,
}

fn category(value: &str) -> &'static str {
    match value.trim().to_ascii_lowercase().as_str() {
        "abuse" => "abuse",
        "attack" | "attacks" => "attacks",
        "malware" => "malware",
        "spam" | "spammer" => "spam",
        "proxy" | "proxies" => "proxy",
        "botnet" | "bot" | "bots" => "botnet",
        "unroutable" | "unallocated" => "unroutable",
        "anonymizers" | "anonymous" | "anonymizer" => "anonymizers",
        _ => "other",
    }
}

/// FireHOL's UTC date format is normalized; unknown formats are preserved.
fn date(value: &str) -> String {
    let fields: Vec<&str> = value.split_whitespace().collect();
    let months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    if fields.len() == 5 || (fields.len() == 6 && fields[4] == "UTC") {
        let parsed = months.iter().position(|m| *m == fields[1]).and_then(|m| {
            Some((
                m + 1,
                fields[2].parse::<u32>().ok()?,
                fields.last()?.parse::<u32>().ok()?,
            ))
        });
        if let Some((month, day, year)) = parsed {
            let clock: Vec<_> = fields[3].split(':').collect();
            if clock.len() == 3
                && day > 0
                && day <= 31
                && year <= 9999
                && clock
                    .iter()
                    .all(|v| v.len() == 2 && v.bytes().all(|b| b.is_ascii_digit()))
            {
                return format!("{year:04}-{month:02}-{day:02}T{}Z", fields[3]);
            }
        }
    }
    if value.len() == 10
        && value.as_bytes()[4] == b'-'
        && value.as_bytes()[7] == b'-'
        && value
            .bytes()
            .enumerate()
            .all(|(i, b)| i == 4 || i == 7 || b.is_ascii_digit())
    {
        return format!("{value}T00:00:00Z");
    }
    if value.len() == 19 && value.as_bytes()[10] == b' ' {
        return format!("{}T{}Z", &value[..10], &value[11..]);
    }
    value.to_owned()
}

pub fn parse_network(value: &str) -> Result<IpNetwork> {
    if value.contains('/') {
        return Ok(value.parse::<IpNetwork>()?.trunc());
    }
    let ip: IpAddr = value.parse()?;
    Ok(IpNetwork::new(ip, if ip.is_ipv4() { 32 } else { 128 })?)
}

fn encode_metadata(values: &[String; 7]) -> Arc<Value> {
    Arc::new(Value::Map(
        FIELDS
            .into_iter()
            .zip(values)
            .map(|(key, value)| {
                (
                    key.to_owned(),
                    Value::Array(vec![Value::Utf8(value.clone())]),
                )
            })
            .collect(),
    ))
}

/// Prune ignored subtrees before recursion; symlinks and hidden directories are skipped.
pub fn discover(root: &Path) -> Result<Vec<PathBuf>> {
    fn walk(dir: &Path, result: &mut Vec<PathBuf>) -> Result<()> {
        for item in fs::read_dir(dir).with_context(|| {
            format!("Cannot read {}; check directory permissions", dir.display())
        })? {
            let item = item?;
            let kind = item.file_type()?;
            let name = item.file_name();
            let name = name.to_string_lossy();
            if kind.is_dir() {
                if !name.ends_with("_country") && !name.starts_with('.') {
                    walk(&item.path(), result)?;
                }
            } else if kind.is_file() && (name.ends_with(".ipset") || name.ends_with(".netset")) {
                result.push(item.path());
            }
        }
        Ok(())
    }
    let mut files = Vec::new();
    walk(root, &mut files)?;
    files.sort();
    Ok(files)
}

fn send(
    entries: &mut Vec<Entry>,
    source: u64,
    metadata: &Arc<Value>,
    tx: &SyncSender<Batch>,
    size: usize,
) -> Result<()> {
    if entries.is_empty() {
        return Ok(());
    }
    let batch = Batch {
        entries: std::mem::replace(entries, Vec::with_capacity(size)),
        source,
        metadata: Arc::clone(metadata),
    };
    tx.send(batch).map_err(|_| {
        anyhow::anyhow!("MMDB consumer stopped; inspect the preceding generation error")
    })
}

fn parse_file(
    path: &Path,
    root: &Path,
    file_id: u32,
    tx: &SyncSender<Batch>,
    size: usize,
) -> Result<()> {
    let file_name = path
        .strip_prefix(root)?
        .to_str()
        .context("Blocklist paths must use UTF-8")?
        .replace('\\', "/");
    let mut values: [String; 7] = std::array::from_fn(|_| String::new());
    values[0] = file_name;
    values[1] = "other".into();
    let mut snapshot = 0u32;
    let mut source = u64::from(file_id) << 32;
    let mut metadata = encode_metadata(&values);
    let mut entries = Vec::with_capacity(size);
    let mut input = BufReader::with_capacity(256 * 1024, File::open(path)?);
    let mut line = String::new();
    let mut line_number = 0u64;
    let mut count = 0u64;
    loop {
        line.clear();
        if input.read_line(&mut line).with_context(|| {
            format!(
                "Cannot read {} at line {}; check encoding and file permissions",
                path.display(),
                line_number + 1
            )
        })? == 0
        {
            break;
        }
        line_number += 1;
        let text = line.trim().trim_start_matches('\u{feff}').trim();
        if let Some(comment) = text.strip_prefix('#') {
            if let Some((key, value)) = comment.split_once(':') {
                let index = match key.trim().to_ascii_lowercase().as_str() {
                    "category" => Some(1),
                    "maintainer" => Some(2),
                    "maintainer url" => Some(3),
                    "list source url" => Some(4),
                    "source file date" => Some(5),
                    "version" => Some(6),
                    _ => None,
                };
                if let Some(index) = index {
                    let value = value.trim();
                    let normalized = match index {
                        1 => category(value).to_owned(),
                        5 => date(value),
                        _ => value.to_owned(),
                    };
                    if values[index] != normalized {
                        send(&mut entries, source, &metadata, tx, size)?;
                        values[index] = normalized;
                        snapshot = snapshot
                            .checked_add(1)
                            .context("Too many metadata changes in one file")?;
                        source = (u64::from(file_id) << 32) | u64::from(snapshot);
                        metadata = encode_metadata(&values);
                    }
                }
            }
            continue;
        }
        let value = text.split('#').next().unwrap_or("").trim();
        if value.is_empty() {
            continue;
        }
        let network = parse_network(value).with_context(|| format!("Invalid IP or CIDR in {} at line {}: {value:?}; correct the source entry before publishing", path.display(), line_number))?;
        entries.push(Entry::new(network, source));
        count += 1;
        if entries.len() >= size {
            send(&mut entries, source, &metadata, tx, size)?;
        }
    }
    send(&mut entries, source, &metadata, tx, size)?;
    eprintln!("INFO parsed_file file={} entries={count}", path.display());
    Ok(())
}

struct TempDir {
    path: PathBuf,
}
impl TempDir {
    fn new(parent: &Path) -> Result<Self> {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let path = parent.join(format!(
            ".firehol-build-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path)?;
        Ok(Self { path })
    }
}
impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

struct Merge {
    readers: Vec<BufReader<File>>,
    heap: BinaryHeap<Reverse<(Entry, usize)>>,
}
impl Merge {
    fn new(paths: &[PathBuf]) -> Result<Self> {
        let mut result = Self {
            readers: Vec::new(),
            heap: BinaryHeap::new(),
        };
        for (index, path) in paths.iter().enumerate() {
            let mut reader = BufReader::with_capacity(64 * 1024, File::open(path)?);
            if let Some(entry) = Entry::read(&mut reader)? {
                result.heap.push(Reverse((entry, index)));
            }
            result.readers.push(reader);
        }
        Ok(result)
    }
    fn next(&mut self) -> Result<Option<Entry>> {
        let Some(Reverse((entry, index))) = self.heap.pop() else {
            return Ok(None);
        };
        if let Some(next) = Entry::read(&mut self.readers[index])? {
            self.heap.push(Reverse((next, index)));
        }
        Ok(Some(entry))
    }
}

fn compact_runs(mut runs: Vec<PathBuf>, dir: &Path) -> Result<Vec<PathBuf>> {
    let mut generation = 0;
    while runs.len() > FAN_IN {
        let mut next = Vec::new();
        for (index, group) in runs.chunks(FAN_IN).enumerate() {
            let path = dir.join(format!("merge-{generation}-{index}.bin"));
            let mut merged = Merge::new(group)?;
            let mut output = BufWriter::new(File::create(&path)?);
            while let Some(entry) = merged.next()? {
                entry.write(&mut output)?;
            }
            output.flush()?;
            drop(output);
            drop(merged);
            for old in group {
                fs::remove_file(old)?;
            }
            next.push(path);
        }
        runs = next;
        generation += 1;
    }
    Ok(runs)
}

pub fn validate_record(record: &Value) -> Result<()> {
    let Value::Map(map) = record else {
        bail!("MMDB record must be a map");
    };
    ensure!(map.len() == FIELDS.len(), "Unexpected MMDB metadata fields");
    let mut length = None;
    for field in FIELDS {
        let Some(Value::Array(values)) = map.get(field) else {
            bail!("MMDB field {field} must be an array");
        };
        ensure!(
            !values.is_empty() && values.iter().all(|v| matches!(v, Value::Utf8(_))),
            "Invalid array in {field}"
        );
        if let Some(length) = length {
            ensure!(values.len() == length, "MMDB arrays are not aligned");
        } else {
            length = Some(values.len());
        }
    }
    Ok(())
}

/// Read the committer timestamp of HEAD in the exact source repository root.
pub fn source_commit_epoch(root: &Path) -> Result<u64> {
    let git = |args: &[&str]| -> Result<String> {
        let result = Command::new("git")
            .arg("-C")
            .arg(root)
            .args(args)
            .output()
            .context("Cannot read source commit date; install Git and use the source checkout")?;
        ensure!(
            result.status.success(),
            "Cannot read source commit date in {}: {}; use a Git source checkout or provide Options.build_epoch",
            root.display(),
            String::from_utf8_lossy(&result.stderr).trim()
        );
        Ok(String::from_utf8(result.stdout)?.trim().to_owned())
    };
    let checkout = git(&["rev-parse", "--show-toplevel"])?;
    ensure!(
        fs::canonicalize(root)? == fs::canonicalize(checkout)?,
        "Source directory must be its own Git repository root; refusing to use a parent repository's commit date"
    );
    let epoch = git(&["show", "--no-patch", "--format=%ct", "HEAD"])?
        .parse::<u64>()
        .context("Source commit timestamp must be a nonnegative Unix timestamp")?;
    Ok(epoch)
}

pub fn generate(root: &Path, output: &Path, options: &Options) -> Result<Stats> {
    ensure!(
        options.threads > 0 && options.batch_size > 0 && options.queue_size > 0,
        "Threads, batch size and queue capacity must be positive"
    );
    let files = discover(root)?;
    ensure!(
        !files.is_empty(),
        "No .ipset or .netset files found outside ignored directories"
    );
    let mut jobs = files
        .iter()
        .enumerate()
        .map(|(id, path)| Ok((u32::try_from(id)?, path, fs::metadata(path)?.len())))
        .collect::<Result<Vec<_>>>()?;
    jobs.sort_by_key(|(id, _, size)| (Reverse(*size), *id));
    let parent = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    fs::create_dir_all(parent)?;
    let temp = TempDir::new(parent)?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(options.threads)
        .build()?;
    let (tx, rx) = sync_channel(options.queue_size);
    let mut sources = BTreeMap::new();
    let mut entries = 0u64;
    let mut runs = Vec::new();
    eprintln!(
        "INFO parsing_started files={} threads={} batch_size={} queue_size={}",
        files.len(),
        options.threads,
        options.batch_size,
        options.queue_size
    );
    std::thread::scope(|scope| -> Result<()> {
        let producer = scope.spawn(move || {
            pool.install(|| {
                jobs.par_iter().try_for_each(|(id, path, _)| {
                    parse_file(path, root, *id, &tx, options.batch_size)
                })
            })
        });
        let result = (|| -> Result<()> {
            // The original sender must be dropped by the producer, so consume until its work ends.
            while let Ok(mut batch) = rx.recv() {
                entries += batch.entries.len() as u64;
                sources.entry(batch.source).or_insert(batch.metadata);
                batch.entries.sort_unstable();
                let path = temp.path.join(format!("run-{}.bin", runs.len()));
                let mut file = BufWriter::new(File::create(&path)?);
                for entry in batch.entries {
                    entry.write(&mut file)?;
                }
                file.flush()?;
                runs.push(path);
            }
            Ok(())
        })();
        drop(rx);
        let parsed = producer.join().map_err(|_| {
            anyhow::anyhow!("Parser thread panicked; inspect source files and retry")
        })?;
        result?;
        parsed?;
        Ok(())
    })?;
    ensure!(
        entries > 0,
        "Input blocklists contain no addresses; refusing to replace the release"
    );
    let runs = compact_runs(runs, &temp.path)?;
    let mut merged = Merge::new(&runs)?;
    let build_epoch = match options.build_epoch {
        Some(epoch) => epoch,
        None => source_commit_epoch(root)?,
    };
    let metadata = MetadataBuilder::new()
        .build_epoch(build_epoch)
        .ip_version(6)
        .database_type("firehol-blocklist-ipsets")
        .description(
            "en",
            "FireHOL IP reputation; arrays with preserved duplicates and overlapping sources",
        )
        .build()?;
    let mut writer = Writer::with_metadata(metadata).merge_strategy(MergeStrategy::DeepMerge);
    let mut stack: Vec<(Entry, Vec<u64>)> = Vec::new();
    // Cache reusable payloads, not entries: repeated contributions remain repeated array values.
    let mut contexts: BTreeMap<Vec<u64>, Arc<Value>> = BTreeMap::new();
    let mut pending = merged.next()?;
    let mut networks = 0u64;
    let mut samples = Vec::new();
    let mut family = 0;
    while let Some(first) = pending {
        if first.family != family {
            // MMDB reserves ::/96 for IPv4. Do not inherit broad IPv6 bogon lists there.
            writer.remove("::/96".parse()?)?;
            stack.clear();
            family = first.family;
        }
        while stack
            .last()
            .is_some_and(|(parent, _)| !parent.contains(first))
        {
            stack.pop();
        }
        let network = first.network()?;
        let mut signature = stack
            .last()
            .map_or_else(Vec::new, |(_, parent)| parent.clone());
        signature.push(first.source);
        pending = merged.next()?;
        while let Some(next) = pending.filter(|n| n.same_network(first)) {
            signature.push(next.source);
            pending = merged.next()?;
        }
        networks += 1;
        let value = if let Some(value) = contexts.get(&signature) {
            Arc::clone(value)
        } else if signature.len() == 1 {
            Arc::clone(
                sources
                    .get(&signature[0])
                    .context("Missing metadata for sorted input")?,
            )
        } else {
            // The actual library implements array concatenation. No hand-written merge or dedup.
            let mut context = Writer::with_metadata(MetadataBuilder::new().ip_version(4).build()?)
                .merge_strategy(MergeStrategy::DeepMerge);
            let context_net = "0.0.0.0/0".parse()?;
            for id in &signature {
                context.insert_value_shared(
                    context_net,
                    Arc::clone(
                        sources
                            .get(id)
                            .context("Missing metadata for sorted input")?,
                    ),
                )?;
            }
            let bytes = context.finish()?;
            let context_reader = Reader::from_bytes(&bytes)?;
            let value = Arc::new(
                context_reader
                    .lookup_value("0.0.0.0".parse()?)?
                    .to_owned_value(),
            );
            if contexts.len() >= 4096 {
                contexts.clear();
            }
            contexts.insert(signature.clone(), Arc::clone(&value));
            value
        };
        writer.insert_value_shared(network, value)?;
        // Frames retain only the small ordered contribution IDs, and only when they have children.
        if pending.is_some_and(|next| first.contains(next)) {
            stack.push((first, signature));
        }
        if samples.len() < 1024 && network.addr() != IpAddr::V6(Ipv6Addr::UNSPECIFIED) {
            samples.push(network.addr());
        }
    }
    drop(merged);
    eprintln!(
        "INFO writing_database input_entries={entries} output_networks={networks} metadata_snapshots={}",
        sources.len()
    );
    let bytes = writer.finish()?;
    let reader = Reader::from_bytes(&bytes)
        .context("Generated MMDB is invalid; previous release will be preserved")?;
    ensure!(
        reader.metadata().build_epoch == build_epoch,
        "Generated MMDB build_epoch does not match the source commit timestamp"
    );
    eprintln!("INFO source_commit_metadata build_epoch={build_epoch}");
    for ip in samples {
        validate_record(&reader.lookup_value(ip)?.to_owned_value())?;
    }
    ensure!(!bytes.is_empty(), "Generated database is empty");
    let staged = temp.path.join("validated.mmdb");
    fs::write(&staged, &bytes)?;
    fs::rename(&staged, output)?;
    eprintln!(
        "INFO database_validated file={} bytes={} input_entries={entries} output_networks={networks}",
        output.display(),
        bytes.len()
    );
    Ok(Stats {
        files: files.len(),
        entries,
        networks,
        bytes: bytes.len() as u64,
    })
}
