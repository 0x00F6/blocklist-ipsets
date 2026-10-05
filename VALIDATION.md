# Validation report

The pipeline is deployed at `0x00F6/blocklist-ipsets`, with `mmdb-pipeline` as the default branch. The complete source build, independent MMDB validation, and first rolling release succeeded on GitHub Actions. A subsequent manual run with the same source SHA succeeded while skipping compilation, generation, and publication.

## Passed checks

- Rust compilation and optimized release build with the pinned library source snapshot.
- Rustfmt and Clippy with warnings denied for the generator.
- 10 Rust integration tests, including exhaustive membership comparison over a `/24`, duplicate retention, overlapping prefixes, IPv6, metadata changes, recursive exclusions, external-sort compaction, and malformed-input recovery.
- 14 Python workflow tests for forced synchronization, missing and failed publication retries, whole-line SHA markers, first-release drafts, upload failure handling, one-release cleanup, repository guards and the independent source parser.
- Python syntax checks, Bash syntax check for bootstrap, and workflow YAML parsing with the hourly schedule verified.
- Real-source sample build from `feodo.ipset`, `dshield.netset`, and `ipsum_7.ipset`: 332 input entries, 332 stored prefixes, 18,291-byte MMDB.
- 14 sample endpoint lookups verified with MaxMind's independent Python reader source, including all expected aligned metadata fields.

## Source snapshots

- FireHOL commit: `3417de0f1f36025827c9a2752c8672e2c5fce097`.
- libmaxminddb-rs commit: `9834fb987a5ec3f0585193a5f0fee8d1aa779a8a` (version 0.3.2).

Direct GitHub/crates.io network access is unavailable in this execution environment. Library and dependency source files were retrieved through the connected GitHub service for local compilation; the deliverable uses the original pinned Git dependency and registry dependencies. The committed lockfile retains the resolved versions and official registry checksums. The clean GitHub runner confirmed the production dependency fetch, locked builds, and full-data resource usage.

## Production verification — 2026-10-05

- Successful build and publication: https://github.com/0x00F6/blocklist-ipsets/actions/runs/37348901683.
- Successful unchanged-source run: https://github.com/0x00F6/blocklist-ipsets/actions/runs/37349128942.
- Rust toolchain: 1.99.0; production pinned dependency and committed lockfile used without modification.
- 7,995,654 parsed contributions; 3,834,721 stored prefixes; 148 metadata snapshots.
- Output: 136,514,099 bytes; independent MaxMind Python reader verified 866 sample lookups.
- Generation time: 4.77 seconds; maximum resident memory: 905,268 KiB on this runner.
- SHA-256: `762506d9110c1f8b5512ef73096c287f88a58ec18ecd57ad68da02571eb76c03`.
- Exactly one published release and one uploaded asset confirmed through the GitHub API. The rolling tag points to the upstream SHA. GitHub also displays its two automatic source archives.
- First-publication recovery tested and exercised: reuse drafts through the release list, upload by exact release ID, create a missing tag, then publish only after upload confirmation and cleanup.
- Hourly schedule: `17 * * * *` UTC, rebuilding only when the upstream SHA has not been successfully published (or explicitly forced).

Rolling release: https://github.com/0x00F6/blocklist-ipsets/releases/tag/firehol-blocklist-ipsets.
