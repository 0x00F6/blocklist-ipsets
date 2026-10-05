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
- Preserve the IPv4/IPv6 MMDB alias handling and independent-reader validation.

## Release lifecycle

- Publish one uploaded asset, `firehol-blocklist-ipsets.mmdb`, in one mutable release/tag, `firehol-blocklist-ipsets`. GitHub's automatic source archives are separate from uploaded assets.
- Validate with both the Rust reader and independent MaxMind Python reader before replacing the asset.
- Recover existing drafts through the release list; upload by exact release ID and create a missing tag before publishing.
- Mark a commit published only after successful upload, tag movement, and cleanup. Enforce the one-release policy on unchanged-source runs too.
- Never store credentials or personal access tokens in the repository. Use the workflow's built-in `GITHUB_TOKEN` with `contents: write`.

## Logs and verification

- Log detailed English messages on one line, with file/line context and actionable errors. Include the synchronized branch, source SHA, input counts, output size, and validation result where relevant.
- Run `make check` before publishing code changes. Keep meaningful coverage for overlap propagation, error cancellation, recursive exclusions, metadata alignment, mirror creation, exact-SHA verification, and publication retries.
- After workflow or synchronization changes, verify a complete GitHub Actions run using this fork's `main` source commit. Preserve the unchanged-source skip behavior.
