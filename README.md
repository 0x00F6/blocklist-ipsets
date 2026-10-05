# 🛡️ FireHOL Blocklist IPsets MMDB

An hourly mirror of [firehol/blocklist-ipsets](https://github.com/firehol/blocklist-ipsets) and a parallel Rust MMDB generator powered by [libmaxminddb-rs](https://github.com/0x00F6/libmaxminddb-rs).

🚀 **One rolling release, two downloads:** the original MMDB and its gzip-9 `.tar.gz` archive.

## Contents

- [Download](#download)
- [Compression and extraction](#compression-and-extraction)
- [Branches and files](#branches-and-files)
- [Automation](#automation)
- [Generation](#generation)
- [MMDB metadata](#mmdb-metadata)
- [Record schema](#record-schema)
- [Local use](#local-use)
- [Validation](#validation)
- [Contributing](#contributing)
- [Source licensing](#source-licensing)

## Download

📦 Both files are published together in the [rolling release](https://github.com/0x00F6/blocklist-ipsets/releases/tag/firehol-blocklist-ipsets).

| Format | Stable download | Use |
| --- | --- | --- |
| MMDB | [firehol-blocklist-ipsets.mmdb](https://github.com/0x00F6/blocklist-ipsets/releases/download/firehol-blocklist-ipsets/firehol-blocklist-ipsets.mmdb) | Open directly with an MMDB reader |
| tar.gz | [firehol-blocklist-ipsets.mmdb.tar.gz](https://github.com/0x00F6/blocklist-ipsets/releases/download/firehol-blocklist-ipsets/firehol-blocklist-ipsets.mmdb.tar.gz) | Smaller download; extract before reading |

The URLs stay the same across updates. Release notes include actual sizes, compression savings, SHA-256 hashes, the source commit, and `build_epoch`. GitHub's automatic source-code archives contain repository sources; the uploaded MMDB archive contains the generated database.

## Compression and extraction

🗜️ The archive uses **gzip level 9**, the maximum gzip compression level, and contains only `firehol-blocklist-ipsets.mmdb`. Its tar entry uses the FireHOL source commit timestamp, fixed permissions, and owner/group IDs of zero. The gzip header has no filename or generation date, making repeated compression reproducible for identical inputs and compression tooling.

```bash
tar -xzf firehol-blocklist-ipsets.mmdb.tar.gz
```

🔐 Before publication, the archive is read through its gzip CRC trailer and its decompressed MMDB is compared with the original by SHA-256. No files are extracted during verification. Both uploaded files are then confirmed by name, size, upload state, and SHA-256 when GitHub provides a digest.

A gzip-9 benchmark on the 2026-10-05 source snapshot reduced **136.51 MB to approximately 54.53 MB**, saving **60.05%**. Exact archive sizes can vary with the source snapshot and compression tooling; use the release's measured sizes and hashes.

## Branches and files

| Branch | Purpose |
| --- | --- |
| `main` | Exact copy of upstream FireHOL `master`, force-synchronized each workflow run |
| `mmdb-pipeline` | Default branch containing the generator, tests, documentation, and scheduled workflow |

🧹 `mmdb-pipeline` contains no FireHOL data snapshots or country directories. Input files and upstream documentation belong to `main`; the workflow checks out its verified source commit into `data/` only when a build is needed.

| Path | Role |
| --- | --- |
| `.github/workflows/hourly.yml` | Hourly synchronization, generation, validation, and publication |
| `src/` | Rust parser and MMDB generator |
| `scripts/` | GitHub API client, mirror synchronization, publication, archive creation, and independent validation |
| `tests/` | Rust integration tests and Python workflow and archive tests |
| `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml` | Dependencies and Rust toolchain configuration |
| `Makefile`, `.gitignore` | Build commands and generated-file exclusions |
| `README.md`, `AGENTS.md` | User documentation and contributor instructions |

## Automation

⏱️ The schedule is `17 * * * *`: every hour at minute 17, in UTC. GitHub can delay scheduled runs.

`mmdb-pipeline` stays the default branch so GitHub discovers the schedule. The workflow runs there and uses the synchronized data commit from `main` for generation.

| Trigger | Behavior |
| --- | --- |
| Hourly schedule | Synchronize `main`; build only if the current upstream SHA has not been successfully published |
| Manual run | Same behavior; enable **Rebuild even if the upstream SHA is already published** to force a rebuild |
| Pipeline code change | Synchronize `main` and rebuild to validate and publish the updated generator |
| README or AGENTS update | Documentation update without an automatic rebuild |

Run it manually from [GitHub Actions](https://github.com/0x00F6/blocklist-ipsets/actions/workflows/hourly.yml): select **Run workflow**, choose `mmdb-pipeline`, and optionally enable the rebuild checkbox.

🔐 The workflow uses the built-in `GITHUB_TOKEN` with `contents: write`. Branch rules must permit forced updates to `main`, tag rules must permit moving `firehol-blocklist-ipsets`, and the release must remain mutable. Repository permissions must allow GitHub Actions.

A failed build is retried even when `main` already matches upstream. Either missing or incomplete file also triggers a rebuild. Publication is marked `pending` before replacing either asset and `complete` only after both uploads, tag movement, and cleanup succeed. An interrupted forced rebuild is retried even for the same source SHA. Unchanged-source runs still enforce one release with exactly two uploaded assets.

## Generation

1. Fetch the current FireHOL `master` SHA and force-update this fork's `main` reference, creating it if missing.
2. Compare that SHA with the last **successfully published** rolling release and check that both assets are uploaded and publication is marked complete. Skip compilation when all checks pass. Missing releases or interrupted builds are retried.
3. Verify that `main` points to that SHA, then check out that exact commit from this fork into `data/`. Read its Git committer timestamp for the MMDB `build_epoch`. Subsequent branch updates cannot change the build inputs.
4. Walk `.ipset` and `.netset` files recursively. Prune every directory ending with `_country`; skip hidden directories and symlinks.
5. Parse files in parallel with Rayon, largest first. Normalize bare IPv4 to `/32` and IPv6 to `/128`. Preserve CIDRs and mask host bits. Parse headers, inline comments, CRLF, BOM and metadata changes within files. Invalid addresses fail with file/line context.
6. Send batches of 32,768 compact entries through a bounded standard-library channel. Sort each batch to disk, then merge runs with a maximum fan-in of 48. No complete input vector or globally locked writer is required.
7. Traverse sorted networks with a prefix stack. Include all ancestor contributions in more-specific records: the library's `DeepMerge` only merges values at an identical prefix, so this propagation is explicit.
8. Build aligned arrays using the actual `MergeStrategy::DeepMerge` writer. Duplicate values remain present. Cache up to 4,096 reusable merged payloads; this cache does not remove duplicate entries. A single writer builds the final IPv4/IPv6 MMDB.
9. Validate the generated MMDB with the Rust reader and sampled source contributions with MaxMind's independent Python reader. Check that `build_epoch` exactly matches the source commit timestamp and log the actual file metadata. Only validated output replaces the local file.
10. Create the gzip-9 tar archive, verify it contains only the expected MMDB, and check decompression against the original SHA-256. Replace the local archive only after verification succeeds.
11. Reuse an existing draft if publication was interrupted. Mark publication pending, upload both files to its exact release ID, confirm both assets, create or update the tag, and remove other releases and extra assets. Mark the source SHA complete only after those operations succeed. Build failures preserve the prior release; an interrupted asset replacement can temporarily leave a download unavailable and is retried on the next run.

The channel, sort runs, readers and payload cache are bounded. The final libmaxminddb-rs trie is in memory, so peak memory still grows with the number of distinct prefixes and metadata combinations. No claim of bounded total memory is made.

## MMDB metadata

🗓️ `build_epoch` is the Unix timestamp of the **committer date of the exact FireHOL source commit**, read with `git show --no-patch --format=%ct HEAD` in `data/`. It represents the data snapshot date and stays unchanged when the same source commit is rebuilt.

| Field | Value |
| --- | --- |
| `database_type` | `firehol-blocklist-ipsets` |
| `description.en` | FireHOL IP reputation; arrays with preserved duplicates and overlapping sources |
| `ip_version` | `6`, covering IPv4 and IPv6 |
| `languages` | `["en"]` |
| `build_epoch` | FireHOL source commit's committer timestamp in Unix seconds |
| Binary format version | `2.0` |
| `node_count`, `record_size` | Computed by the writer |

The source directory must be the root of its own Git checkout. The CLI fails if the commit date cannot be read. When using an exported source archive through the Rust API, provide its known source timestamp explicitly in `Options.build_epoch`.

The independent validator compares the stored timestamp with the checked-out source commit and logs all global metadata on one line. For example, commit `3417de0f1f36025827c9a2752c8672e2c5fce097` has `build_epoch = 1791191852`, corresponding to `2026-10-05T09:17:32Z`.

## Record schema

All fields are parallel arrays. At each index, the values describe the same source contribution. Empty strings preserve alignment when a header is absent.

```json
{
  "files": ["feodo.ipset", "ipsum_7.ipset"],
  "categories": ["malware", "abuse"],
  "maintainers": ["Abuse.ch", "stamparm"],
  "maintainer_urls": ["https://feodotracker.abuse.ch/", ""],
  "source_urls": ["https://feodotracker.abuse.ch/downloads/ipblocklist_recommended.txt", ""],
  "source_file_dates": ["2026-03-12T07:15:03Z", ""],
  "versions": ["549", ""]
}
```

`Update Frequency` is ignored and never stored. Categories use the firewall parser's canonical labels: abuse, attacks, malware, spam, proxy, botnet, unroutable, anonymizers, other. FireHOL UTC timestamps become ISO 8601; unknown date formats remain unchanged. Arrays are retained exactly, including repeated categories, files and sources. Ordering is deterministic: broad prefixes first, then source file order and metadata snapshots.

The library's `simd` feature is enabled. Array concatenation uses the library's implementation; no extra SIMD acceleration claim is made without a benchmark.

MMDB conventionally reserves `::/96` for IPv4 and supports IPv6 transition aliases. The generator isolates IPv4 from broad IPv6 parent metadata. Independent identities in IPv4-compatible or mapped IPv6 ranges are subject to the reader's alias semantics.

## Local use

🦀 Requires Rust stable >= 1.98.1, Python 3, Git, and sufficient disk/RAM for the source snapshot. The writer is pinned to commit `9834fb987a5ec3f0585193a5f0fee8d1aa779a8a`.

```bash
git clone --depth=1 --single-branch --branch=mmdb-pipeline https://github.com/0x00F6/blocklist-ipsets.git firehol-mmdb
cd firehol-mmdb
git clone --depth=1 --single-branch --branch=main https://github.com/0x00F6/blocklist-ipsets.git data
make check
make generate DATA=data OUTPUT=dist/firehol-blocklist-ipsets.mmdb
python3 -m pip install maxminddb
python3 scripts/validate.py data dist/firehol-blocklist-ipsets.mmdb
make archive DATA=data OUTPUT=dist/firehol-blocklist-ipsets.mmdb
```

Set `FIREHOL_PARSER_THREADS` to override available CPU count. The Rust API also exposes batch size and queue capacity for testing and embedding.

## Validation

✅ `make check` runs Rustfmt, Clippy, Rust integration tests, and Python workflow and archive tests. Tests cover duplicates, array alignment, overlapping prefixes, IPv4/IPv6, directory exclusions, metadata changes, disk-run compaction, invalid inputs, exact `main` synchronization, failed publication retries (including a partial two-file upload of the same source SHA), first-release recovery, deterministic archive headers, gzip corruption, and decompression integrity. A reference test checks every address in a `/24` against all source memberships.

The first full production build on 2026-10-05 used 149 source files and passed independent MaxMind Python-reader validation:

| Metric | Result |
| --- | ---: |
| Parsed source contributions | 7,995,654 |
| Stored prefixes | 3,834,721 |
| MMDB size | 136,514,099 bytes |
| Independent sample lookups | 866 |
| Generation time on that runner | 4.77 seconds |
| Maximum resident memory on that runner | 905,268 KiB |

These measurements describe that source snapshot and runner; later snapshots can differ.

## Contributing

🛠️ Read [AGENTS.md](AGENTS.md) before changing the pipeline. Keep all generator changes on `mmdb-pipeline`, keep `main` an exact upstream mirror, and run `make check` before publishing changes. Preserve both stable asset names, parallel-array metadata, duplicate contributions, and successful-publication retry logic.

## Source licensing

Blocklist contents remain governed by the individual upstream sources and FireHOL's original licensing notices, preserved with the source snapshot on `main`. Review those terms before redistributing or commercially using the generated data.
