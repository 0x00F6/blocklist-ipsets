"""Small GitHub REST client; never log authorization headers."""
import json
import os
import urllib.error
import urllib.request


class GitHub:
    def __init__(self, repository=None):
        self.repository = repository or os.environ["GITHUB_REPOSITORY"]
        if self.repository != "0x00F6/blocklist-ipsets":
            raise ValueError("This publisher is restricted to 0x00F6/blocklist-ipsets")
        self.token = os.environ["GH_TOKEN"]

    def request(self, method, path, data=None, missing_ok=False):
        payload = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(
            "https://api.github.com" + path, data=payload, method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise RuntimeError(f"GitHub {method} {path} failed: HTTP {error.code}; check Actions permissions and repository rules") from error

    def releases(self):
        result, page = [], 1
        while True:
            batch = self.request("GET", f"/repos/{self.repository}/releases?per_page=100&page={page}")
            result.extend(batch)
            if len(batch) < 100:
                return result
            page += 1
