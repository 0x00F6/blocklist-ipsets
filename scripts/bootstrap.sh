#!/usr/bin/env bash
# Run from this generator directory with gh authenticated as 0x00F6.
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
repo='0x00F6/blocklist-ipsets'
test "$(gh api user --jq .login)" = '0x00F6' || { echo 'Authenticate gh as 0x00F6 first.' >&2; exit 1; }
if ! gh api "repos/$repo" >/dev/null 2>&1; then
  gh repo fork firehol/blocklist-ipsets --clone=false
fi
test "$(gh api "repos/$repo" --jq '.parent.full_name')" = 'firehol/blocklist-ipsets' || { echo 'Existing target is not the expected fork.' >&2; exit 1; }
checkout_dir="$(mktemp -d)"
trap 'rm -rf -- "$checkout_dir"' EXIT
gh repo clone "$repo" "$checkout_dir/repo" -- --depth=1 --branch=master
cd "$checkout_dir/repo"
if gh api "repos/$repo/branches/mmdb-pipeline" >/dev/null 2>&1; then
  echo 'mmdb-pipeline already exists; review it before rerunning bootstrap.' >&2
  exit 1
fi
git switch -c mmdb-pipeline
for item in Cargo.toml Cargo.lock rust-toolchain.toml .gitignore AGENTS.md Makefile README.md src tests scripts .github; do
  if [ -e "$source_dir/$item" ]; then cp -R -- "$source_dir/$item" .; fi
done
git config user.name '0x00F6'
git config user.email '155492361+0x00F6@users.noreply.github.com'
git add Cargo.toml Cargo.lock rust-toolchain.toml .gitignore AGENTS.md Makefile README.md src tests scripts .github
git commit -m 'Add hourly FireHOL mirror and parallel DeepMerge MMDB release pipeline'
git push origin mmdb-pipeline
gh api --method PATCH "repos/$repo" -f default_branch=mmdb-pipeline
gh api --method PUT "repos/$repo/actions/permissions" -F enabled=true -f allowed_actions=all
gh workflow run hourly.yml --repo "$repo" --ref mmdb-pipeline -f force=true
echo "Pipeline installed: https://github.com/$repo/actions"
