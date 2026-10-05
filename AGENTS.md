# Contributor instructions

## Branch ownership and repository layout

- Keep `main` an exact commit-level mirror of `firehol/blocklist-ipsets` branch `master`. Synchronization may force-update only `main`.
- Keep `mmdb-pipeline` as the default branch. All generator code, tests, workflow configuration, `README.md`, and this file live there.
- Keep `mmdb-pipeline` limited to `.github/workflows/hourly.yml`, `.gitignore`, `AGENTS.md`, `README.md`, `Makefile`, Cargo/toolchain files, `src/`, `scripts/`, and `tests/`. Do not commit upstream data, country directories, build outputs, caches, or standalone validation reports.
- Preserve the README table of contents, branch documentation, download links, and concise emoji cues when updating documentation. Write technical documentation and logs in English.

## Synchronization and build inputs

- Read the upstream `master` SHA, create or force-update `main`, and verify that `main` points to exactly that SHA before emitting workflow outputs.
- Check out that immutable SHA from this fork into `data/`. Do not generate from the pipeline branch or a moving branch reference.
- Run the schedule hourly at minute 17 UTC. Preserve manual forced rebuilds and automatic rebuilds for pipeline code changes.
- Compare the verified source SHA against the last successfully published release. Retry failed or missing publication even if `main` is already synchronized.

## Parsing and MMDB records

- Parse `.ipset` and `.netset` recursively, pruning every directory ending in `_country` and all hidden directories. Do not follow symlinks.
- Normalize bare IPv4 and IPv6 addresses to `/32` and `/128`. Preserve CIDR prefixes and mask host bits.
- Preserve all contributions, including duplicates. Store aligned arrays; use empty strings for missing optional metadata. Never store update frequency.
- Use the pinned libmaxminddb-rs writer with `MergeStrategy::DeepMerge`. Preserve metadata from overlapping ancestor networks in more-specific records.
- Use Rayon for parallel parsing and bounded batches. Keep the writer on one consumer thread. External sorting must cap open files and memory.
- Set global MMDB `build_epoch` to the committer timestamp (`%ct`) of the exact FireHOL source commit. Read it from the source repository root; never use the pipeline commit, author date, file modification time, or generation clock. Reject parent-repository fallback. Explicit timestamps through the Rust API must describe the source snapshot.
- Independently verify `build_epoch` against the source checkout and log the actual global MMDB metadata. Rebuilding the same source snapshot must preserve its timestamp and produce identical bytes.
- Preserve the IPv4/IPv6 MMDB alias handling and independent-reader validation.

## Release lifecycle

- Publish exactly two uploaded assets, `firehol-blocklist-ipsets.mmdb` and `firehol-blocklist-ipsets.mmdb.tar.gz`, in one mutable release/tag, `firehol-blocklist-ipsets`. Preserve both stable names and download links. GitHub's automatic source archives are separate from uploaded assets.
- Validate the MMDB with both the Rust reader and independent MaxMind Python reader before creating the archive or replacing remote assets.
- Use maximum gzip level 9 and USTAR with exactly one MMDB entry. Set its timestamp to the source commit epoch, permissions to 0644, and owner/group IDs to zero; omit gzip filename and generation date. Verify the gzip CRC and decompressed SHA-256 against the original before uploading. Replace local archive output only after verification succeeds.
- Recover existing drafts through the release list; upload by exact release ID and create a missing tag before publishing.
- Mark publication pending before either upload, then confirm both assets by name, size, uploaded state, and digest when available. Mark the source SHA complete only after both uploads, tag movement, and cleanup. Retry partial uploads even for forced rebuilds of the same SHA. Enforce one release with exactly the two assets on unchanged-source runs too.
- Generate release notes with both download links, actual sizes, compression savings, extraction instructions, SHA-256 hashes, source SHA, and source-date build_epoch.
- Never store credentials or personal access tokens in the repository. Use the workflow's built-in `GITHUB_TOKEN` with `contents: write`.

## Logs and verification

- Log detailed English messages on one line, with file/line context and actionable errors. Include the synchronized branch, source SHA, input counts, output size, and validation result where relevant.
- Run `make check` before publishing code changes. Keep meaningful coverage for overlap propagation, error cancellation, recursive exclusions, metadata alignment, mirror creation, exact-SHA verification, publication retries after partial two-file uploads, deterministic archive headers, corrupt gzip rejection, and decompression integrity.
- After workflow or synchronization changes, verify a complete GitHub Actions run using this fork's `main` source commit. Preserve the unchanged-source skip behavior.
