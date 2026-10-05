# FireHOL Blocklist IPsets MMDB

An hourly mirror of [firehol/blocklist-ipsets](https://github.com/firehol/blocklist-ipsets) and a parallel Rust generator using [libmaxminddb-rs](https://github.com/0x00F6/libmaxminddb-rs).

**One rolling release, one database:** `firehol-blocklist-ipsets.mmdb`.

```text
https://github.com/0x00F6/blocklist-ipsets/releases/download/firehol-blocklist-ipsets/firehol-blocklist-ipsets.mmdb
```

The download becomes available after the first successful workflow publication.

## Branches

| Branch | Purpose |
| --- | --- |
| `master` | Exact FireHOL upstream mirror, force-synchronized each run |
| `mmdb-pipeline` | Default branch containing this generator and its scheduled workflow |

The workflow must live on a separate default branch so forcing `master` cannot delete it. The scheduler runs at minute 17 of every hour, in UTC. GitHub may delay scheduled runs. `workflow_dispatch` supports a forced rebuild, and pushes that change the pipeline also rebuild it.

## Generation

1. Fetch the current FireHOL `master` SHA and force-update the fork's `master` reference.
2. Compare that SHA with the last **successfully published** rolling release and check that the asset is complete. Skip compilation when both match. Missing releases or interrupted builds are retried.
3. Check out the exact source SHA, independent of subsequent upstream changes.
4. Walk `.ipset` and `.netset` files recursively. Prune every directory ending with `_country`; skip hidden directories and symlinks.
5. Parse files in parallel with Rayon, largest first. Normalize bare IPv4 to `/32` and IPv6 to `/128`. Preserve CIDRs and mask host bits. Parse headers, inline comments, CRLF, BOM and metadata changes within files. Invalid addresses fail with file/line context.
6. Send batches of 32,768 compact entries through a bounded standard-library channel. Sort each batch to disk, then merge runs with a maximum fan-in of 48. No complete input vector or globally locked writer is required.
7. Traverse sorted networks with a prefix stack. Include all ancestor contributions in more-specific records: the library's `DeepMerge` only merges values at an identical prefix, so this propagation is explicit.
8. Build aligned arrays using the actual `MergeStrategy::DeepMerge` writer. Duplicate values remain present. Cache up to 4,096 reusable merged payloads; this cache does not remove duplicate entries. A single writer builds the final IPv4/IPv6 MMDB.
9. Validate the generated MMDB with the Rust reader and sampled source contributions with MaxMind's independent Python reader. Only validated output replaces the local file.
10. Replace the stable release asset, confirm the upload, update the tag and remove other releases. Mark the source SHA completed only after those operations succeed. Build failures preserve the prior release; an interrupted asset replacement can temporarily leave the download unavailable and is retried on the next run.

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

Rust stable >= 1.98.1, Python 3, Git, and enough disk/RAM for the FireHOL snapshot are required. The writer is pinned to commit `9834fb987a5ec3f0585193a5f0fee8d1aa779a8a`.

```bash
git clone --depth=1 https://github.com/firehol/blocklist-ipsets.git data
make check
make generate DATA=data OUTPUT=dist/firehol-blocklist-ipsets.mmdb
python3 -m pip install maxminddb
python3 scripts/validate.py data dist/firehol-blocklist-ipsets.mmdb
```

Set `FIREHOL_PARSER_THREADS` to override available CPU count. The Rust API also exposes batch size and queue capacity for testing and embedding.

## Install this pipeline

Authenticate GitHub CLI as `0x00F6` with repository, Actions and workflow write access. From this directory:

```bash
bash scripts/bootstrap.sh
```

The script creates or verifies the fork, publishes `mmdb-pipeline`, makes it the default branch, enables Actions and dispatches the first build. It stops if that branch already exists so existing pipeline changes cannot be overwritten. The workflow uses only its built-in `GITHUB_TOKEN` with `contents: write`; no personal access token is stored in the repository. Branch/tag rules must allow the mirror and rolling tag updates, and rolling releases must be mutable.

The workflow enforces exactly one release in this fork, including removing any other releases and extra assets. The stable release is named and tagged `firehol-blocklist-ipsets`.

## Validation

`make check` checks Rust formatting, Clippy, Rust integration tests and Python workflow tests. Tests cover duplicates, metadata alignment, nested prefix inheritance, IPv4/IPv6, ignored directories, metadata changes, disk-run compaction, invalid input, failed publication retries and first-release creation. A reference test checks every address in a `/24` against all source memberships.

Blocklist contents remain governed by the individual upstream sources and FireHOL's original licensing notices. Review those terms before redistributing or commercially using the generated data.
