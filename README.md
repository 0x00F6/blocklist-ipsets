# 🛡️ FireHOL Blocklist IPsets MMDB

An hourly mirror of [firehol/blocklist-ipsets](https://github.com/firehol/blocklist-ipsets) and a parallel Rust MMDB generator powered by [libmaxminddb-rs](https://github.com/0x00F6/libmaxminddb-rs).

🚀 **One rolling release, one uploaded database:** `firehol-blocklist-ipsets.mmdb`.

## Contents

- [Download](#download)
- [Branches and files](#branches-and-files)
- [Automation](#automation)
- [Generation](#generation)
- [Record schema](#record-schema)
- [Local use](#local-use)
- [Validation](#validation)
- [Contributing](#contributing)
- [Source licensing](#source-licensing)

## Download

📦 [Download the latest MMDB](https://github.com/0x00F6/blocklist-ipsets/releases/download/firehol-blocklist-ipsets/firehol-blocklist-ipsets.mmdb) · [View the rolling release](https://github.com/0x00F6/blocklist-ipsets/releases/tag/firehol-blocklist-ipsets)

The download URL stays the same across updates. GitHub additionally provides its automatic source-code archives.

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
| `scripts/` | GitHub API client, mirror synchronization, publication, and independent validation |
| `tests/` | Rust integration tests and Python workflow tests |
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

A failed build is retried even when `main` already matches upstream. A missing or incomplete asset also triggers a rebuild. Unchanged-source runs still enforce the one-release and one-uploaded-asset policy.

## Generation

1. Fetch the current FireHOL `master` SHA and force-update this fork's `main` reference, creating it if missing.
2. Compare that SHA with the last **successfully published** rolling release and check that the asset is complete. Skip compilation when both match. Missing releases or interrupted builds are retried.
3. Verify that `main` points to that SHA, then check out that exact commit from this fork into `data/`. Subsequent branch updates cannot change the build inputs.
4. Walk `.ipset` and `.netset` files recursively. Prune every directory ending with `_country`; skip hidden directories and symlinks.
5. Parse files in parallel with Rayon, largest first. Normalize bare IPv4 to `/32` and IPv6 to `/128`. Preserve CIDRs and mask host bits. Parse headers, inline comments, CRLF, BOM and metadata changes within files. Invalid addresses fail with file/line context.
6. Send batches of 32,768 compact entries through a bounded standard-library channel. Sort each batch to disk, then merge runs with a maximum fan-in of 48. No complete input vector or globally locked writer is required.
7. Traverse sorted networks with a prefix stack. Include all ancestor contributions in more-specific records: the library's `DeepMerge` only merges values at an identical prefix, so this propagation is explicit.
8. Build aligned arrays using the actual `MergeStrategy::DeepMerge` writer. Duplicate values remain present. Cache up to 4,096 reusable merged payloads; this cache does not remove duplicate entries. A single writer builds the final IPv4/IPv6 MMDB.
9. Validate the generated MMDB with the Rust reader and sampled source contributions with MaxMind's independent Python reader. Only validated output replaces the local file.
10. Reuse an existing draft if publication was interrupted, upload to its exact release ID, confirm the asset, create or update the tag, and remove other releases. Mark the source SHA completed only after those operations succeed. Build failures preserve the prior release; an interrupted asset replacement can temporarily leave the download unavailable and is retried on the next run.

The channel, sort runs, readers and payload cache are bounded. The final libmaxminddb-rs trie is in memory, so peak memory still grows with the number of distinct prefixes and metadata combinations. No claim of bounded total memory is made.

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
```

Set `FIREHOL_PARSER_THREADS` to override available CPU count. The Rust API also exposes batch size and queue capacity for testing and embedding.

## Validation

✅ `make check` runs Rustfmt, Clippy, Rust integration tests, and Python workflow tests. Tests cover duplicates, array alignment, overlapping prefixes, IPv4/IPv6, directory exclusions, metadata changes, disk-run compaction, invalid inputs, exact `main` synchronization, failed publication retries, and first-release recovery. A reference test checks every address in a `/24` against all source memberships.

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

🛠️ Read [AGENTS.md](AGENTS.md) before changing the pipeline. Keep all generator changes on `mmdb-pipeline`, keep `main` an exact upstream mirror, and run `make check` before publishing changes. Preserve the stable asset name, parallel-array metadata, duplicate contributions, and successful-publication retry logic.

## Source licensing

Blocklist contents remain governed by the individual upstream sources and FireHOL's original licensing notices, preserved with the source snapshot on `main`. Review those terms before redistributing or commercially using the generated data.
