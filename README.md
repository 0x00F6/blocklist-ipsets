<p align="center">
  <img src="docs/assets/firehol-mmdb.png" alt="FireHOL MMDB network shield logo" width="280">
</p>

# 🛡️ FireHOL Blocklist IPsets MMDB

An hourly mirror of [firehol/blocklist-ipsets](https://github.com/firehol/blocklist-ipsets) and a parallel Rust MMDB generator powered by [libmaxminddb-rs](https://github.com/0x00F6/libmaxminddb-rs).

🚀 **One rolling release, two downloads:** the original MMDB and its gzip-9 `.tar.gz` archive.

## Contents

- [Project website](#project-website)
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

## Project website

🌐 [FireHOL MMDB project site](https://0x00f6.github.io/blocklist-ipsets/) — a single-page English overview with downloads, pipeline details, data schema, and live release sizes and SHA-256 hashes.

🔎 [FireHOL IP Lists](https://iplists.firehol.org/) — explore the upstream feeds, categories, maintainers, and list overlaps.

## Download

📦 Both files are published together in the [rolling release](https://github.com/0x00F6/blocklist-ipsets/releases/tag/firehol-blocklist-ipsets). Each successful build publishes a new release before deleting the previous one, renewing GitHub's displayed publication date while keeping the same download links.

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

The verified production archive for source commit `3417de0f1f36025827c9a2752c8672e2c5fce097` reduces **136.51 MB to 53.60 MB**, saving **60.74%**. Exact archive sizes can vary with the source snapshot and compression tooling; use the release's measured sizes and hashes.

## Branches and files

| Branch | Purpose |
| --- | --- |
| `main` | Exact copy of upstream FireHOL `master`, force-synchronized each workflow run |
| `mmdb-pipeline` | Default branch containing the generator, tests, documentation, and scheduled workflow |

🧹 `mmdb-pipeline` contains no FireHOL data snapshots or country directories. Input files and upstream documentation belong to `main`; the workflow checks out its verified source commit into `data/` only when a build is needed.

| Path | Role |
| --- | --- |
| `.github/workflows/hourly.yml` | Hourly synchronization, generation, validation, and publication |
| `.github/workflows/pages.yml` | Deploy the project website to GitHub Pages |
| `docs/` | Static single-page project website and shared logo |
| `src/` | Rust parser and MMDB generator |
| `scripts/` | GitHub API client, mirror synchronization, publication, archive creation, and independent validation |
| `tests/` | Rust integration tests and Python workflow and archive tests |
| `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml` | Dependencies and Rust toolchain configuration |
| `Makefile`, `.gitignore` | Build commands and generated-file exclusions |
| `README.md`, `AGENTS.md` | User documentation and contributor instructions |

## Automation

⏱️ The schedule is `7 * * * *`: every hour at minute 7, in UTC. GitHub can delay scheduled runs.

`mmdb-pipeline` stays the default branch so GitHub discovers the schedule. The workflow runs there and uses the synchronized data commit from `main` for generation.

| Trigger | Behavior |
| --- | --- |
| Cloudflare `firehol-updated` dispatch | Synchronize and build only if the upstream SHA has not been successfully published |
| Hourly fallback schedule | Synchronize `main`; build only if the current upstream SHA has not been successfully published |
| Manual run | Same behavior; enable **Rebuild even if the upstream SHA is already published** to force a rebuild |
| Pipeline code change | Synchronize `main` and rebuild to validate and publish the updated generator |
| README or AGENTS update | Documentation update without an automatic rebuild |

Run it manually from [GitHub Actions](https://github.com/0x00F6/blocklist-ipsets/actions/workflows/hourly.yml): select **Run workflow**, choose `mmdb-pipeline`, and optionally enable the rebuild checkbox.

🔐 The workflow uses the built-in `GITHUB_TOKEN` with `contents: write`. Branch rules must permit forced updates to `main`; tag rules must permit moving `firehol-blocklist-ipsets` and creating/deleting the pipeline's `firehol-blocklist-ipsets-staging-*` tags. Releases must remain mutable. Repository permissions must allow GitHub Actions.

A failed build is retried even when `main` already matches upstream. Either missing or incomplete file also triggers a rebuild. A pending replacement also triggers a retry, including a failed forced rebuild of the same source SHA. The previous release remains available while the replacement is prepared. Publication becomes `complete` only after both files are confirmed in the new published release, the old releases are deleted, and the stable tag and staging cleanup succeed. Unchanged-source runs still enforce one release with exactly two uploaded assets.

### Cloudflare monitor (every 30 minutes)

The Worker in `scripts/cloudflare-firehol/` checks upstream `master` at minute **00 and 30 UTC**, then sends `repository_dispatch` with type `firehol-updated` only when the SHA differs from its last accepted dispatch. The first poll also dispatches. GitHub independently fetches and verifies the official upstream SHA: it does not trust the payload as a build input. The existing hourly schedule remains a retry fallback for failed builds and missed dispatches.

**Deployment requires your Cloudflare account and a GitHub token; committing these files does not activate the Worker.** From a checkout of `mmdb-pipeline` with Node.js 22+:

```bash
cd scripts/cloudflare-firehol
node --test worker.test.mjs
npx wrangler@4 login
npx wrangler@4 kv namespace create STATE
```

Replace `REPLACE_WITH_KV_NAMESPACE_ID` in `wrangler.jsonc` with the returned namespace ID, then:

```bash
npx wrangler@4 secret put GITHUB_TOKEN
npx wrangler@4 deploy
npx wrangler@4 tail
```

Enter the GitHub token only at Wrangler's secret prompt. Use a fine-grained token restricted to `0x00F6/blocklist-ipsets`, with **Contents: write** for repository dispatch. Public upstream reads use the same authenticated token. Do not use the workflow's ephemeral `GITHUB_TOKEN` as this persistent Worker secret. Renew the token before expiry.

In Cloudflare, verify the `firehol-commit-monitor` Worker has the `STATE` KV binding, `GITHUB_TOKEN` secret, and cron `*/30 * * * *`. Cron configuration changes can take up to 15 minutes to propagate. The next tick logs `dispatched` or `unchanged`; check the matching GitHub Actions run and its final conclusion. Logs never include the token. The Worker has no public HTTP endpoint.

KV is updated only after GitHub accepts a dispatch. A failed dispatch is retried next tick. A failure between dispatch and KV persistence, overlapping invocations, or KV propagation may cause a duplicate dispatch; GitHub concurrency and the successful-release SHA check make repeats safe. An accepted dispatch is not proof of a successful build: the hourly fallback handles failed publication. Polling observes the current branch tip and can coalesce multiple commits between ticks.

## Generation

1. Fetch the current FireHOL `master` SHA and force-update this fork's `main` reference, creating it if missing.
2. Compare that SHA with the last **successfully published** rolling release and check that both assets are uploaded, publication is marked complete, and no pending replacement exists. Skip compilation when all checks pass. Missing releases or interrupted builds are retried.
3. Verify that `main` points to that SHA, then check out that exact commit from this fork into `data/`. Read its Git committer timestamp for the MMDB `build_epoch`. Subsequent branch updates cannot change the build inputs.
4. Walk `.ipset` and `.netset` files recursively. Prune every directory ending with `_country`; skip hidden directories and symlinks.
5. Parse files in parallel with Rayon, largest first. Normalize bare IPv4 to `/32` and IPv6 to `/128`. Preserve CIDRs and mask host bits. Parse headers, inline comments, CRLF, BOM and metadata changes within files. Invalid addresses fail with file/line context.
6. Send batches of 32,768 compact entries through a bounded standard-library channel. Sort each batch to disk, then merge runs with a maximum fan-in of 48. No complete input vector or globally locked writer is required.
7. Traverse sorted networks with a prefix stack. Include all ancestor contributions in more-specific records: the library's `DeepMerge` only merges values at an identical prefix, so this propagation is explicit.
8. Build aligned arrays using the actual `MergeStrategy::DeepMerge` writer. Duplicate values remain present. Cache up to 4,096 reusable merged payloads; this cache does not remove duplicate entries. A single writer builds the final IPv4/IPv6 MMDB.
9. Validate the generated MMDB with the Rust reader and sampled source contributions with MaxMind's independent Python reader. Check that `build_epoch` exactly matches the source commit timestamp and log the actual file metadata. Only validated output replaces the local file.
10. Create the gzip-9 tar archive, verify it contains only the expected MMDB, and check decompression against the original SHA-256. Replace the local archive only after verification succeeds.
11. Create a new draft under a unique temporary staging tag, or resume an interrupted replacement for the same source SHA. Mark only that replacement pending, upload both files to its exact release ID, and confirm their sizes, uploaded state, and hashes. Publish the replacement and confirm both files again while the previous release is still available.
12. Delete the previous releases, move `firehol-blocklist-ipsets` to the source SHA, and assign that stable tag to the new release. Remove staging tags and extra assets, then mark publication complete. The new release ID renews GitHub's publication date; stable download URLs stay the same. A brief cutover occurs when the stable tag is reassigned. If interrupted there, the new published release remains recoverable and the next run restores the stable links.

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

✅ `make check` runs Rustfmt, Clippy, Rust integration tests, and Python workflow and archive tests. Tests cover duplicates, array alignment, overlapping prefixes, IPv4/IPv6, directory exclusions, metadata changes, disk-run compaction, invalid inputs, exact `main` synchronization, publication before deleting the previous release, retries after uploads/publication/deletion/tag handover (including forced rebuilds of the same source SHA), first-release recovery, deterministic archive headers, gzip corruption, and decompression integrity. A reference test checks every address in a `/24` against all source memberships.

The first full production build on 2026-10-05 used 149 source files and passed independent MaxMind Python-reader validation:

| Metric | Result |
| --- | ---: |
| Parsed source contributions | 7,995,654 |
| Stored prefixes | 3,834,721 |
| MMDB size | 136,514,099 bytes |
| Verified gzip-9 archive size for the same snapshot | 53,599,273 bytes |
| Archive size reduction | 60.74% |
| Independent sample lookups | 866 |
| Generation time on that runner | 4.77 seconds |
| Maximum resident memory on that runner | 905,268 KiB |

These measurements describe that source snapshot and runner; later snapshots can differ.

## Contributing

🛠️ Read [AGENTS.md](AGENTS.md) before changing the pipeline. Keep all generator changes on `mmdb-pipeline`, keep `main` an exact upstream mirror, and run `make check` before publishing changes. Preserve both stable asset names, parallel-array metadata, duplicate contributions, and successful-publication retry logic.

## Source licensing

Blocklist contents remain governed by the individual upstream sources and FireHOL's original licensing notices, preserved with the source snapshot on `main`. Review those terms before redistributing or commercially using the generated data.
