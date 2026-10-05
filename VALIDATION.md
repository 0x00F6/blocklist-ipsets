# Validation report

The implementation and local validation are complete, and the GitHub fork is created at `0x00F6/blocklist-ipsets`. Pipeline deployment is in progress. The complete upstream build and first rolling release still need confirmation from GitHub Actions.

## Passed checks

- Rust compilation and optimized release build with the pinned library source snapshot.
- Rustfmt and Clippy with warnings denied for the generator.
- 10 Rust integration tests, including exhaustive membership comparison over a `/24`, duplicate retention, overlapping prefixes, IPv6, metadata changes, recursive exclusions, external-sort compaction, and malformed-input recovery.
- 12 Python workflow tests for forced synchronization, missing and failed publication retries, whole-line SHA markers, first-release drafts, upload failure handling, one-release cleanup, repository guards and the independent source parser.
- Python syntax checks, Bash syntax check for bootstrap, and workflow YAML parsing with the hourly schedule verified.
- Real-source sample build from `feodo.ipset`, `dshield.netset`, and `ipsum_7.ipset`: 332 input entries, 332 stored prefixes, 18,291-byte MMDB.
- 14 sample endpoint lookups verified with MaxMind's independent Python reader source, including all expected aligned metadata fields.

## Source snapshots

- FireHOL commit: `3417de0f1f36025827c9a2752c8672e2c5fce097`.
- libmaxminddb-rs commit: `9834fb987a5ec3f0585193a5f0fee8d1aa779a8a` (version 0.3.2).

Direct GitHub/crates.io network access is unavailable in this execution environment. Library and dependency source files were retrieved through the connected GitHub service for local compilation; the deliverable uses the original pinned Git dependency and registry dependencies. The committed lockfile retains the resolved versions and official registry checksums. A clean GitHub runner must still confirm the production dependency fetch and full-data resource usage before the first release can be considered live.
