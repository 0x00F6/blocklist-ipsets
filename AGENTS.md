# Contributor instructions

- Keep `master` an exact upstream mirror. All generator code and workflows live on `mmdb-pipeline`, the default branch.
- Parse `.ipset` and `.netset` recursively, pruning every directory ending in `_country` and all hidden directories. Do not follow symlinks.
- Normalize bare IPv4 and IPv6 addresses to `/32` and `/128`. Preserve CIDR prefixes and mask host bits.
- Preserve all contributions, including duplicates. Store aligned arrays; use empty strings for missing optional metadata. Never store update frequency.
- Use the pinned libmaxminddb-rs writer with `MergeStrategy::DeepMerge`. Keep metadata from overlapping ancestor networks in more-specific records.
- Use Rayon for parallel parsing and bounded batches. Keep the writer on one consumer thread. External sorting must cap open files and memory.
- Publish one asset, `firehol-blocklist-ipsets.mmdb`, in one mutable release/tag, `firehol-blocklist-ipsets`. Validate before replacing the previous asset.
- Mark a commit published only after successful upload. A failed build must be retried even if the fork has already synced.
- Log detailed English messages on one line, with file/line context and actionable errors.
- Run `make check` and test overlap propagation, error cancellation, recursive exclusions, metadata alignment, and publication retries before changes are published.

