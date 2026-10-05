"""Force-sync the mirror and compare against the last completed publication."""
import argparse
import os
from github_api import GitHub

TAG = "firehol-blocklist-ipsets"
ASSET = TAG + ".mmdb"
MIRROR = "main"


def needs_build(release, upstream_sha, force=False):
    if force or release is None or release.get("draft"):
        return True
    marker = f"Upstream commit: {upstream_sha}"
    complete = any(asset["name"] == ASSET and asset.get("size", 0) > 0 and asset.get("state") == "uploaded" for asset in release.get("assets", []))
    return marker not in release.get("body", "").splitlines() or not complete


def run(api, force=False, emit=None):
    repo = api.request("GET", f"/repos/{api.repository}")
    if not repo.get("fork") or repo.get("parent", {}).get("full_name") != "firehol/blocklist-ipsets":
        raise RuntimeError("Refusing to sync: target must be a fork of firehol/blocklist-ipsets")
    if repo["default_branch"] != "mmdb-pipeline":
        raise RuntimeError("Set mmdb-pipeline as the default branch before enabling the schedule")
    sha = api.request("GET", "/repos/firehol/blocklist-ipsets/branches/master")["commit"]["sha"]
    # Force only main. Workflow code stays on the separate default branch.
    reference = api.request("GET", f"/repos/{api.repository}/git/ref/heads/{MIRROR}", missing_ok=True)
    if reference is None:
        api.request("POST", f"/repos/{api.repository}/git/refs", {"ref": f"refs/heads/{MIRROR}", "sha": sha})
    else:
        api.request("PATCH", f"/repos/{api.repository}/git/refs/heads/{MIRROR}", {"sha": sha, "force": True})
    mirrored_sha = api.request("GET", f"/repos/{api.repository}/branches/{MIRROR}")["commit"]["sha"]
    if mirrored_sha != sha:
        raise RuntimeError(f"Mirror verification failed: {MIRROR} points to {mirrored_sha}, expected {sha}; retry synchronization")
    release = api.request("GET", f"/repos/{api.repository}/releases/tags/{TAG}", missing_ok=True)
    changed = needs_build(release, sha, force)
    if not changed:
        # Enforce the one-release policy even when no rebuild is needed.
        for existing in api.releases():
            if existing["id"] != release["id"]:
                api.request("DELETE", f"/repos/{api.repository}/releases/{existing['id']}")
        for extra in release.get("assets", []):
            if extra["name"] != ASSET:
                api.request("DELETE", f"/repos/{api.repository}/releases/assets/{extra['id']}")
    print(f"INFO mirror_synced branch={MIRROR} upstream_sha={sha} build_required={str(changed).lower()}")
    if emit:
        emit("upstream_sha", sha)
        emit("changed", str(changed).lower())
    return sha, changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    def emit(key, value):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"{key}={value}\n")
    run(GitHub(), args.force, emit)


if __name__ == "__main__":
    main()
