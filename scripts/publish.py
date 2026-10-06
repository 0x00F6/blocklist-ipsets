"""Publish the validated MMDB and its gzip-9 archive in a fresh release before retiring the previous one."""
import argparse
import datetime
from pathlib import Path
import re
import uuid
from github_api import GitHub
from sync import ASSET, ARCHIVE, ASSETS, TAG, STAGING_PREFIX, pending_publication
from archive import verify_archive


def set_tag(api, name, sha):
    tag = api.request("GET", f"/repos/{api.repository}/git/ref/tags/{name}", missing_ok=True)
    if tag is None:
        api.request("POST", f"/repos/{api.repository}/git/refs", {"ref": f"refs/tags/{name}", "sha": sha})
    else:
        api.request("PATCH", f"/repos/{api.repository}/git/refs/tags/{name}", {"sha": sha, "force": True})
    confirmed = api.request("GET", f"/repos/{api.repository}/git/ref/tags/{name}")
    if confirmed["object"]["sha"] != sha:
        raise RuntimeError(f"Tag {name} does not point to source SHA {sha}; next cron will retry")


def confirm_release(api, release_id, asset, archive, stats, published=False):
    confirmed = api.request("GET", f"/repos/{api.repository}/releases/{release_id}")
    for path, digest in ((asset, stats["mmdb_sha256"]), (archive, stats["archive_sha256"])):
        if not any(a["name"] == path.name and a.get("state") == "uploaded" and a.get("size") == path.stat().st_size and a.get("digest") in (None, "sha256:" + digest) for a in confirmed.get("assets", [])):
            raise RuntimeError(f"Uploaded asset {path.name} was not confirmed; next cron will retry this SHA")
    if published and confirmed.get("draft"):
        raise RuntimeError("New release is still a draft; preserving the previous release")
    return confirmed


def cleanup_staging_tags(api):
    refs = api.request("GET", f"/repos/{api.repository}/git/matching-refs/tags/{STAGING_PREFIX}")
    for reference in refs:
        name = reference["ref"]
        if not name.startswith("refs/tags/" + STAGING_PREFIX):
            raise RuntimeError(f"Refusing to delete unrelated ref {name}")
        api.request("DELETE", f"/repos/{api.repository}/git/{name}", missing_ok=True)


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
    releases = api.releases()
    if any(item.get("immutable") for item in releases):
        raise RuntimeError("Disable immutable releases before replacing this rolling release")
    # Never overwrite the completed release. Reuse only an interrupted replacement.
    candidates = [item for item in releases if pending_publication(item)
                  and f"Upstream commit: {sha}" in item.get("body", "").splitlines()]
    release = max(candidates, key=lambda item: (not item.get("draft"), item["id"])) if candidates else None
    if release is None:
        staging_tag = STAGING_PREFIX + uuid.uuid4().hex
        release = api.request("POST", f"/repos/{api.repository}/releases", {
            "tag_name": staging_tag, "target_commitish": sha, "name": TAG,
            "body": f"Upstream commit: {sha}\nPublication status: pending", "draft": True,
        })
    pending = [line for line in release.get("body", "").splitlines() if not line.startswith("Publication status:")]
    pending.append("Publication status: pending")
    api.request("PATCH", f"/repos/{api.repository}/releases/{release['id']}", {"body": "\n".join(pending)})
    if upload is None:
        def upload(path):
            api.upload_asset(release, path)
    for path in (asset, archive):
        upload(path)
    confirm_release(api, release["id"], asset, archive, stats)
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
        "Each successful build publishes a new release before retiring the previous one. The stable tag and download links are preserved. GitHub also supplies its automatic source-code archives.",
    ])
    # A published tag can belong to only one release. Publish under a unique
    # temporary tag first, then retire the old release and take its stable tag.
    set_tag(api, release["tag_name"], sha)
    pending_body = body.replace("Publication status: complete", "Publication status: pending")
    api.request("PATCH", f"/repos/{api.repository}/releases/{release['id']}", {
        "name": TAG, "body": pending_body, "draft": False, "prerelease": False, "make_latest": "true",
    })
    confirmed = confirm_release(api, release["id"], asset, archive, stats, published=True)
    # Deletion starts only after the replacement is published with both files.
    for existing in api.releases():
        if existing["id"] != release["id"]:
            api.request("DELETE", f"/repos/{api.repository}/releases/{existing['id']}")
    set_tag(api, TAG, sha)
    api.request("PATCH", f"/repos/{api.repository}/releases/{release['id']}", {"tag_name": TAG, "target_commitish": sha})
    canonical = api.request("GET", f"/repos/{api.repository}/releases/tags/{TAG}")
    if canonical["id"] != release["id"]:
        raise RuntimeError("Stable tag does not resolve to the new release; next cron will retry")
    for extra in confirmed.get("assets", []):
        if extra["name"] not in ASSETS:
            api.request("DELETE", f"/repos/{api.repository}/releases/assets/{extra['id']}")
    cleanup_staging_tags(api)
    api.request("PATCH", f"/repos/{api.repository}/releases/{release['id']}", {"body": body, "make_latest": "true"})
    print(f"INFO release_published release_id={release['id']} tag={TAG} upstream_sha={sha} mmdb_bytes={asset.stat().st_size} archive_bytes={archive.stat().st_size} build_epoch={build_epoch}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("asset")
    parser.add_argument("sha")
    parser.add_argument("--build-epoch", required=True, type=int)
    args = parser.parse_args()
    publish(GitHub(), args.asset, args.sha, args.build_epoch)
