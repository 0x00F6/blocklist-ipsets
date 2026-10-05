"""Publish the validated MMDB and its gzip-9 archive in one rolling release."""
import argparse
import datetime
from pathlib import Path
import re
from github_api import GitHub
from sync import ASSET, ARCHIVE, ASSETS, TAG
from archive import verify_archive


def publish(api, asset, sha, build_epoch, upload=None):
    asset = Path(asset)
    if asset.name != ASSET or not asset.is_file() or asset.stat().st_size == 0:
        raise ValueError("Expected a validated, nonempty firehol-blocklist-ipsets.mmdb")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a full upstream commit SHA")
    if not isinstance(build_epoch, int) or build_epoch < 0:
        raise ValueError("Expected the source commit's nonnegative Unix timestamp")
    archive = asset.with_name(ARCHIVE)
    if not archive.is_file() or archive.stat().st_size == 0:
        raise ValueError("Expected a validated, nonempty firehol-blocklist-ipsets.mmdb.tar.gz")
    stats = verify_archive(asset, archive, build_epoch)
    source_date = datetime.datetime.fromtimestamp(build_epoch, datetime.timezone.utc).isoformat(timespec="seconds")
    release = api.request("GET", f"/repos/{api.repository}/releases/tags/{TAG}", missing_ok=True)
    if release is None:
        # The tag endpoint omits drafts. Reuse an interrupted first publication.
        drafts = [item for item in api.releases() if item["tag_name"] == TAG]
        release = min(drafts, key=lambda item: item["id"]) if drafts else None
    if release and release.get("immutable"):
        raise RuntimeError("Disable immutable releases for this rolling release")
    if release is None:
        release = api.request("POST", f"/repos/{api.repository}/releases", {
            "tag_name": TAG, "target_commitish": sha, "name": TAG,
            "body": "First validated MMDB and archive upload in progress.\nPublication status: pending", "draft": True,
        })
    # A forced rebuild of the same source SHA must also be retried after partial upload.
    pending = [line for line in release.get("body", "").splitlines() if not line.startswith("Publication status:")]
    pending.append("Publication status: pending")
    api.request("PATCH", f"/repos/{api.repository}/releases/{release['id']}", {"body": "\n".join(pending)})
    if upload is None:
        def upload(path):
            api.upload_asset(release, path)
    # Preserve the previous SHA until both uploads and cleanup succeed.
    for path in (asset, archive):
        upload(path)
    confirmed = api.request("GET", f"/repos/{api.repository}/releases/{release['id']}")
    for path, digest in ((asset, stats["mmdb_sha256"]), (archive, stats["archive_sha256"])):
        if not any(a["name"] == path.name and a.get("state") == "uploaded" and a.get("size") == path.stat().st_size and a.get("digest") in (None, "sha256:" + digest) for a in confirmed.get("assets", [])):
            raise RuntimeError(f"Uploaded asset {path.name} was not confirmed; next cron will retry this SHA")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    reduction = 100 * (1 - stats["archive_bytes"] / stats["mmdb_bytes"])
    base_url = f"https://github.com/{api.repository}/releases/download/{TAG}"
    body = "\n".join([
        "# 🛡️ FireHOL Blocklist IPsets MMDB", "",
        "Upstream repository: firehol/blocklist-ipsets", f"Upstream commit: {sha}",
        "Publication status: complete", f"Published: {now}", "",
        "## 📦 Downloads", "",
        "| Format | Download | Size (bytes) | Size (MB) |", "| --- | --- | ---: | ---: |",
        f"| MMDB | [{ASSET}]({base_url}/{ASSET}) | {stats['mmdb_bytes']:,} | {stats['mmdb_bytes']/1_000_000:.2f} |",
        f"| tar.gz · gzip -9 | [{ARCHIVE}]({base_url}/{ARCHIVE}) | {stats['archive_bytes']:,} | {stats['archive_bytes']/1_000_000:.2f} |",
        "", f"Compression: gzip level 9; size reduction **{reduction:.2f}%**. The archive contains only `{ASSET}` and is verified against the original by SHA-256.",
        "", "Extract with:", "", "```bash", f"tar -xzf {ARCHIVE}", "```", "",
        "## 🗓️ Source snapshot and metadata", "",
        f"MMDB build_epoch: {build_epoch}", f"Source commit date (UTC): {source_date}",
        "`build_epoch` uses the FireHOL source commit's committer date and remains unchanged when that source commit is rebuilt.",
        "", "Generator: libmaxminddb-rs (pinned commit)", "Merge strategy: DeepMerge", "",
        "Inputs: *.ipset and *.netset; *_country directories excluded.",
        "Bare IPv4/IPv6 addresses use /32 and /128. Overlapping sources are preserved.",
        "Metadata: parallel arrays with duplicates; missing values are empty strings.",
        "Fields: files, categories, maintainers, maintainer_urls, source_urls, source_file_dates, versions. Update frequency is ignored.",
        "", "## 🔐 SHA-256", "", "```text",
        f"{stats['mmdb_sha256']}  {ASSET}", f"{stats['archive_sha256']}  {ARCHIVE}", "```", "",
        "One rolling release with two uploaded assets. GitHub also supplies its automatic source-code archives.",
    ])
    # Complete tag movement and cleanup before marking this SHA published.
    tag = api.request("GET", f"/repos/{api.repository}/git/ref/tags/{TAG}", missing_ok=True)
    if tag is None:
        # Draft releases do not create their tag until publication.
        api.request("POST", f"/repos/{api.repository}/git/refs", {"ref": f"refs/tags/{TAG}", "sha": sha})
    else:
        api.request("PATCH", f"/repos/{api.repository}/git/refs/tags/{TAG}", {"sha": sha, "force": True})
    for existing in api.releases():
        if existing["id"] != release["id"]:
            api.request("DELETE", f"/repos/{api.repository}/releases/{existing['id']}")
    # Keep exactly the two requested downloadable files.
    for extra in confirmed.get("assets", []):
        if extra["name"] not in ASSETS:
            api.request("DELETE", f"/repos/{api.repository}/releases/assets/{extra['id']}")
    api.request("PATCH", f"/repos/{api.repository}/releases/{release['id']}", {"name": TAG, "body": body, "draft": False, "prerelease": False, "make_latest": "true"})
    print(f"INFO release_published tag={TAG} upstream_sha={sha} mmdb_bytes={asset.stat().st_size} archive_bytes={archive.stat().st_size} build_epoch={build_epoch}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("asset")
    parser.add_argument("sha")
    parser.add_argument("--build-epoch", required=True, type=int)
    args = parser.parse_args()
    publish(GitHub(), args.asset, args.sha, args.build_epoch)
