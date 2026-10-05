import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from github_api import GitHub
from publish import publish
from sync import ASSET, TAG, needs_build, run
from validate import samples, source_files, validate

SHA = "a" * 40
OLD = "b" * 40


def release(sha=OLD):
    return {"id": 1, "tag_name": TAG, "body": f"Upstream commit: {sha}", "draft": False,
            "assets": [{"id": 9, "name": ASSET, "size": 4, "state": "uploaded"}]}


class FakeGitHub:
    repository = "0x00F6/blocklist-ipsets"

    def __init__(self, existing=True):
        self.current = release() if existing else None
        self.tag = {"object": {"sha": OLD}} if existing else None
        self.mirror = OLD
        self.mirror_mismatch = False
        self.calls = []
        self.repo = {"fork": True, "parent": {"full_name": "firehol/blocklist-ipsets"}, "default_branch": "mmdb-pipeline"}
        self.other = [{"id": 2, "tag_name": "old"}]

    def request(self, method, path, data=None, missing_ok=False):
        self.calls.append((method, path, data))
        if path.endswith("/branches/master"):
            return {"commit": {"sha": SHA}}
        if path.endswith("/branches/main"):
            return {"commit": {"sha": OLD if self.mirror_mismatch else self.mirror}}
        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": self.mirror}} if self.mirror else None
        if method == "PATCH" and path.endswith("/git/refs/heads/main"):
            self.mirror = data["sha"]
            return {"object": {"sha": self.mirror}}
        if method == "GET" and path == "/repos/" + self.repository:
            return copy.deepcopy(self.repo)
        if method == "GET" and "/git/ref/tags/" in path:
            return copy.deepcopy(self.tag)
        if method == "POST" and path.endswith("/git/refs"):
            if data["ref"] == "refs/heads/main":
                self.mirror = data["sha"]
                return {"object": {"sha": self.mirror}}
            self.tag = {"object": {"sha": data["sha"]}}
            return copy.deepcopy(self.tag)
        if method == "PATCH" and "/git/refs/tags/" in path:
            if self.tag is None:
                raise RuntimeError("Missing tag: draft releases do not create refs")
            self.tag["object"]["sha"] = data["sha"]
            return copy.deepcopy(self.tag)
        if method == "GET" and "/releases/" in path:
            if "/releases/tags/" in path and self.current and self.current["draft"]:
                return None
            return copy.deepcopy(self.current)
        if method == "POST" and path.endswith("/releases"):
            self.current = dict(data, id=1, assets=[])
            return copy.deepcopy(self.current)
        if method == "PATCH" and path.endswith("/releases/1"):
            self.current.update(data)
            return copy.deepcopy(self.current)
        return None

    def releases(self):
        return ([self.current] if self.current else []) + self.other


class WorkflowTests(unittest.TestCase):
    def test_no_changes_skips_build_but_force_rebuilds(self):
        self.assertFalse(needs_build(release(SHA), SHA))
        self.assertTrue(needs_build(release(SHA), SHA, force=True))

    def test_failed_or_missing_publication_is_retried(self):
        for item in (None, release(OLD), dict(release(SHA), assets=[]), dict(release(SHA), draft=True)):
            self.assertTrue(needs_build(item, SHA))

    def test_sha_marker_must_match_a_whole_line(self):
        item = release(SHA)
        item["body"] += "suffix"
        self.assertTrue(needs_build(item, SHA))

    def test_sync_forces_only_main_and_verifies_the_upstream_commit(self):
        api = FakeGitHub()
        self.assertEqual(run(api), (SHA, True))
        patches = [c for c in api.calls if c[0] == "PATCH"]
        self.assertEqual(patches, [("PATCH", "/repos/0x00F6/blocklist-ipsets/git/refs/heads/main", {"sha": SHA, "force": True})])
        self.assertEqual(api.mirror, SHA)

    def test_missing_main_is_created_from_the_exact_upstream_commit(self):
        api = FakeGitHub()
        api.mirror = None
        self.assertEqual(run(api), (SHA, True))
        self.assertEqual(api.mirror, SHA)
        self.assertIn(("POST", "/repos/0x00F6/blocklist-ipsets/git/refs", {"ref": "refs/heads/main", "sha": SHA}), api.calls)
        self.assertFalse(any(c[0] == "PATCH" for c in api.calls))

    def test_mismatched_main_fails_before_emitting_outputs_or_pruning(self):
        api = FakeGitHub()
        api.mirror_mismatch = True
        outputs = []
        with self.assertRaisesRegex(RuntimeError, "Mirror verification failed"):
            run(api, emit=lambda *args: outputs.append(args))
        self.assertEqual(outputs, [])
        self.assertFalse(any(c[0] == "DELETE" for c in api.calls))

    def test_sync_checks_parent_and_default_branch_before_mutation(self):
        for wrong in ({"fork": False}, {"parent": {"full_name": "wrong/repo"}}, {"default_branch": "master"}):
            api = FakeGitHub()
            api.repo.update(wrong)
            with self.assertRaises(RuntimeError):
                run(api)
            self.assertFalse(any(c[0] == "PATCH" for c in api.calls))

    def test_repository_guard(self):
        with self.assertRaises(ValueError):
            GitHub("firehol/blocklist-ipsets")

    def test_successful_publish_prunes_then_marks_commit_complete(self):
        api = FakeGitHub()
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            publish(api, asset, SHA, upload=lambda path: None)
        self.assertEqual(api.current["body"].splitlines()[3], "Upstream commit: " + SHA)
        update = next(i for i,c in enumerate(api.calls) if c[0] == "PATCH" and c[1].endswith("/releases/1"))
        prune = next(i for i,c in enumerate(api.calls) if c[0] == "DELETE")
        self.assertLess(prune, update)
        self.assertEqual(api.calls[prune][1], "/repos/0x00F6/blocklist-ipsets/releases/2")
        self.assertEqual(api.tag["object"]["sha"], SHA)
        self.assertFalse(any(c[0] == "POST" and c[1].endswith("/git/refs") for c in api.calls))

    def test_no_change_sync_still_enforces_one_release(self):
        api = FakeGitHub()
        api.current = release(SHA)
        self.assertEqual(run(api), (SHA, False))
        self.assertTrue(any(c[0] == "DELETE" and c[1].endswith("/releases/2") for c in api.calls))

    def test_upload_failure_preserves_marker_and_does_not_prune(self):
        api = FakeGitHub()
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            def fail(_):
                raise RuntimeError("upload failed")
            with self.assertRaises(RuntimeError):
                publish(api, asset, SHA, upload=fail)
        self.assertEqual(api.current["body"], "Upstream commit: " + OLD)
        self.assertFalse(any(c[0] in ("DELETE", "PATCH") for c in api.calls))

    def test_first_publication_stays_draft_until_confirmed_upload(self):
        api = FakeGitHub(existing=False)
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            def upload(_):
                self.assertTrue(api.current["draft"])
                api.current["assets"] = release()["assets"]
            publish(api, asset, SHA, upload=upload)
        self.assertFalse(api.current["draft"])
        self.assertFalse(needs_build(api.current, SHA))
        self.assertEqual(api.tag["object"]["sha"], SHA)

    def test_retry_of_uploaded_draft_creates_missing_tag(self):
        api = FakeGitHub()
        api.current["draft"] = True
        api.tag = None
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            publish(api, asset, SHA, upload=lambda path: None)
        self.assertEqual(api.tag["object"]["sha"], SHA)
        self.assertFalse(needs_build(api.current, SHA))
        self.assertFalse(any(c[0] == "POST" and c[1].endswith("/releases") for c in api.calls))

    def test_invalid_asset_never_touches_remote(self):
        api = FakeGitHub()
        with self.assertRaises(ValueError):
            publish(api, "/missing/wrong.mmdb", SHA)
        self.assertEqual(api.calls, [])

    def test_upload_targets_release_id_and_streams_asset(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', GH_TOKEN='test-token'):
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            api = GitHub('0x00F6/blocklist-ipsets')
            def send(request, timeout):
                self.assertIn('/releases/1/assets?name=' + ASSET, request.full_url)
                self.assertEqual(request.get_header('Content-length'), '4')
                self.assertEqual(request.data.read(), b'mmdb')
                from unittest.mock import MagicMock
                response = MagicMock()
                response.__enter__.return_value.read.return_value = b'{"id":10}'
                return response
            with patch.object(api, 'request') as calls, patch('urllib.request.urlopen', side_effect=send):
                self.assertEqual(api.upload_asset(release(), asset), {"id": 10})
                calls.assert_called_once_with('DELETE', '/repos/0x00F6/blocklist-ipsets/releases/assets/9')

    def test_independent_parser_preserves_metadata_changes_and_country_pruning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.ipset").write_text("# Category: bots\n# Source File Date: Thu Mar 12 07:15:03 UTC 2026\n192.0.2.1\n# Maintainer: B\n192.0.2.2\n", encoding="utf-8")
            (root / "bad_country").mkdir()
            (root / "bad_country" / "bad.ipset").write_text("invalid")
            self.assertEqual(len(list(source_files(root))), 1)
            result = list(samples(root))
            self.assertEqual(result[0][1][1], "botnet")
            self.assertEqual(result[0][1][5], "2026-03-12T07:15:03Z")
            self.assertEqual(result[-1][1][2], "B")

    def test_independent_validation_rejects_a_generation_date_instead_of_source_date(self):
        from unittest.mock import MagicMock
        reader = MagicMock()
        reader.metadata.return_value.database_type = TAG
        reader.metadata.return_value.build_epoch = 1_791_191_853
        opener = MagicMock()
        opener.return_value.__enter__.return_value = reader
        with patch('validate.source_commit_epoch', return_value=1_791_191_852):
            with self.assertRaisesRegex(ValueError, "does not match source commit timestamp"):
                validate('data', 'database.mmdb', open_database=opener)
        reader.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
