"""Replace the rolling asset, then mark the SHA complete and prune other releases."""
import argparse
import datetime
from pathlib import Path
import re
import subprocess
from github_api import GitHub
from sync import ASSET, TAG


def publish(api, asset, sha, upload=None):
    asset = Path(asset)
    if asset.name != ASSET or not asset.is_file() or asset.stat().st_size == 0:
        raise ValueError("Expected a validated, nonempty firehol-blocklist-ipsets.mmdb")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a full upstream commit SHA")
    release = api.request("GET", f"/repos/{api.repository}/releases/tags/{TAG}", missing_ok=True)
    if release and release.get("immutable"):
        raise RuntimeError("Disable immutable releases for this rolling release")
    if release is None:
        release = api.request("POST", f"/repos/{api.repository}/releases", {
            "tag_name": TAG, "target_commitish": sha, "name": TAG,
            "body": "First validated MMDB upload in progress.", "draft": True,
        })
    if upload is None:
        def upload(path):
            subprocess.run(["gh", "release", "upload", TAG, str(path), "--repo", api.repository, "--clobber"], check=True)
    # On existing releases the previous successful SHA remains until upload succeeds.
    upload(asset)
    confirmed = api.request("GET", f"/repos/{api.repository}/releases/{release['id']}")
    if not any(a["name"] == ASSET and a.get("state") == "uploaded" and a.get("size") == asset.stat().st_size for a in confirmed.get("assets", [])):
        raise RuntimeError("Uploaded asset was not confirmed; next cron will retry this SHA")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    body = "\n".join([
        "FireHOL Blocklist IPsets MMDB", "",
        "Upstream repository: firehol/blocklist-ipsets", f"Upstream commit: {sha}",
        f"Generated: {now}", "Generator: libmaxminddb-rs (pinned commit)",
        "Merge strategy: DeepMerge", "",
        "Inputs: *.ipset and *.netset; *_country directories excluded.",
        "Bare IPv4/IPv6 addresses use /32 and /128. Overlapping sources are preserved.",
        "Metadata: parallel arrays with duplicates; missing values are empty strings.",
        "",
        f"Download: https://github.com/{api.repository}/releases/download/{TAG}/{ASSET}",
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
    # The rolling release contains exactly the requested MMDB asset.
    for extra in confirmed.get("assets", []):
        if extra["name"] != ASSET:
            api.request("DELETE", f"/repos/{api.repository}/releases/assets/{extra['id']}")
    api.request("PATCH", f"/repos/{api.repository}/releases/{release['id']}", {"name": TAG, "body": body, "draft": False, "prerelease": False, "make_latest": "true"})
    print(f"INFO release_published tag={TAG} upstream_sha={sha} bytes={asset.stat().st_size}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("asset")
    parser.add_argument("sha")
    args = parser.parse_args()
    publish(GitHub(), args.asset, args.sha)
