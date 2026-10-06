import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from github_api import GitHub
from publish import publish
from sync import ASSET, ARCHIVE, TAG, STAGING_PREFIX, needs_build, pending_publication, run
from archive import create_archive
from validate import samples, source_files, validate

SHA = "a" * 40
OLD = "b" * 40
EPOCH = 1_791_191_852


def release(sha=OLD):
    return {"id": 1, "tag_name": TAG, "body": f"Upstream commit: {sha}\nPublication status: complete", "draft": False, "published_at": "old-publication",
            "assets": [{"id": 9, "name": ASSET, "size": 4, "state": "uploaded"},
                       {"id": 10, "name": ARCHIVE, "size": 1, "state": "uploaded"}]}


class FakeGitHub:
    repository = "0x00F6/blocklist-ipsets"

    def __init__(self, existing=True):
        self.items = {1: release()} if existing else {}
        self.items[2] = dict(release(), id=2, tag_name="old", assets=[])
        self.tags = {TAG: {"object": {"sha": OLD}}} if existing else {}
        self.next_id = 100
        self.mirror = OLD
        self.mirror_mismatch = False
        self.calls = []
        self.repo = {"fork": True, "parent": {"full_name": "firehol/blocklist-ipsets"}, "default_branch": "mmdb-pipeline"}
        self.fail_publish = self.fail_retag = self.fail_tag_cleanup = False
        self.keep_draft_on_publish = False
        self.fail_delete_id = None

    @property
    def current(self):
        matches = [item for item in self.items.values() if item["tag_name"] == TAG]
        return max(matches, key=lambda item: item["id"]) if matches else None

    @current.setter
    def current(self, item):
        for key in [key for key, value in self.items.items() if value["tag_name"] == TAG]:
            del self.items[key]
        if item:
            self.items[item["id"]] = item

    @property
    def replacement(self):
        candidates = [item for item in self.items.values() if pending_publication(item)]
        return max(candidates, key=lambda item: item["id"]) if candidates else None

    @property
    def tag(self):
        return self.tags.get(TAG)

    @tag.setter
    def tag(self, value):
        if value is None:
            self.tags.pop(TAG, None)
        else:
            self.tags[TAG] = value

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
            return copy.deepcopy(self.tags.get(path.split("/tags/")[1]))
        if method == "GET" and "/git/matching-refs/tags/" in path:
            prefix = path.split("/tags/")[1].split("?")[0]
            return [{"ref": "refs/tags/" + name} for name in self.tags if name.startswith(prefix)]
        if method == "POST" and path.endswith("/git/refs"):
            if data["ref"] == "refs/heads/main":
                self.mirror = data["sha"]
                return {"object": {"sha": self.mirror}}
            name = data["ref"].removeprefix("refs/tags/")
            if name in self.tags:
                raise RuntimeError("Ref already exists")
            self.tags[name] = {"object": {"sha": data["sha"]}}
            return copy.deepcopy(self.tags[name])
        if method == "PATCH" and "/git/refs/tags/" in path:
            name = path.split("/tags/")[1]
            if name not in self.tags:
                raise RuntimeError("Missing tag: draft releases do not create refs")
            self.tags[name]["object"]["sha"] = data["sha"]
            return copy.deepcopy(self.tags[name])
        if method == "DELETE" and "/git/refs/tags/" in path:
            if self.fail_tag_cleanup:
                raise RuntimeError("Tag cleanup failed")
            self.tags.pop(path.split("/tags/")[1], None)
            return None
        if method == "GET" and "/releases/tags/" in path:
            name = path.split("/releases/tags/")[1]
            matches = [item for item in self.items.values() if item["tag_name"] == name and not item.get("draft")]
            return copy.deepcopy(max(matches, key=lambda item: item["id"])) if matches else None
        if method == "POST" and path.endswith("/releases"):
            item = dict(data, id=self.next_id, assets=[], published_at=None)
            self.items[self.next_id] = item
            self.next_id += 1
            return copy.deepcopy(item)
        if "/releases/assets/" in path and method == "DELETE":
            asset_id = int(path.rsplit("/", 1)[1])
            for item in self.items.values():
                item["assets"] = [a for a in item.get("assets", []) if a["id"] != asset_id]
            return None
        if "/releases/" in path:
            release_id = int(path.rsplit("/", 1)[1])
            if method == "GET":
                return copy.deepcopy(self.items[release_id])
            if method == "DELETE":
                if release_id == self.fail_delete_id:
                    raise RuntimeError("Old release deletion failed")
                del self.items[release_id]
                return None
            if method == "PATCH":
                item = self.items[release_id]
                if self.keep_draft_on_publish and data.get("draft") is False:
                    data = dict(data, draft=True)
                if self.fail_publish and data.get("draft") is False:
                    raise RuntimeError("New release publication failed")
                if self.fail_retag and data.get("tag_name") == TAG:
                    raise RuntimeError("Stable tag handover failed")
                name = data.get("tag_name", item["tag_name"])
                if not data.get("draft", item["draft"]) and any(other["id"] != release_id and other["tag_name"] == name and not other["draft"] for other in self.items.values()):
                    raise RuntimeError("Two published releases cannot share a tag")
                if item["draft"] and data.get("draft") is False:
                    item["published_at"] = f"new-publication-{release_id}"
                item.update(data)
                return copy.deepcopy(item)
        raise AssertionError(f"Unexpected request {method} {path}")

    def upload_asset(self, release, path):
        item = self.items[release["id"]]
        item["assets"] = [a for a in item["assets"] if a["name"] != path.name]
        item["assets"].append({"id": item["id"] * 10 + (0 if path.name == ASSET else 1), "name": path.name,
                               "size": path.stat().st_size, "state": "uploaded", "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()})

    def releases(self):
        return copy.deepcopy(list(self.items.values()))


class WorkflowTests(unittest.TestCase):
    def test_no_changes_skips_build_but_force_rebuilds(self):
        self.assertFalse(needs_build(release(SHA), SHA))
        self.assertTrue(needs_build(release(SHA), SHA, force=True))

    def test_failed_or_missing_publication_is_retried(self):
        for item in (None, release(OLD), dict(release(SHA), assets=[]), dict(release(SHA), draft=True)):
            self.assertTrue(needs_build(item, SHA))

    def test_sha_marker_must_match_a_whole_line(self):
        item = release(SHA)
        item["body"] = f"Upstream commit: {SHA}suffix\nPublication status: complete"
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

    def publish_fixture(self, api, sha=SHA, upload=None):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            create_archive(asset, EPOCH)
            publish(api, asset, sha, EPOCH, upload=upload)

    def assert_complete(self, api):
        self.assertEqual(len(api.items), 1)
        self.assertIsNotNone(api.current)
        self.assertFalse(api.current["draft"])
        self.assertEqual(api.current["tag_name"], TAG)
        self.assertEqual({a["name"] for a in api.current["assets"]}, {ASSET, ARCHIVE})
        self.assertFalse(needs_build(api.current, SHA))
        self.assertEqual(api.tag["object"]["sha"], SHA)
        self.assertFalse(any(name.startswith(STAGING_PREFIX) for name in api.tags))

    def test_new_release_is_published_before_old_release_is_deleted(self):
        api = FakeGitHub()
        old = copy.deepcopy(api.current)
        def upload(path):
            self.assertEqual(api.current, old)
            self.assertTrue(api.replacement["draft"])
            api.upload_asset(api.replacement, path)
        self.publish_fixture(api, upload=upload)
        self.assertNotEqual(api.current["id"], old["id"])
        self.assertNotEqual(api.current["published_at"], old["published_at"])
        publish_index = next(i for i,c in enumerate(api.calls) if c[0] == "PATCH" and c[2].get("draft") is False)
        delete_index = next(i for i,c in enumerate(api.calls) if c[0] == "DELETE" and c[1].endswith("/releases/1"))
        retag_index = next(i for i,c in enumerate(api.calls) if c[0] == "PATCH" and c[2].get("tag_name") == TAG)
        complete_index = next(i for i,c in enumerate(api.calls) if c[0] == "PATCH" and "Publication status: complete" in c[2].get("body", ""))
        self.assertLess(publish_index, delete_index)
        self.assertLess(delete_index, retag_index)
        self.assertLess(retag_index, complete_index)
        self.assert_complete(api)

    def test_no_change_sync_still_enforces_one_release(self):
        api = FakeGitHub()
        api.current = release(SHA)
        self.assertEqual(run(api), (SHA, False))
        self.assertTrue(any(c[0] == "DELETE" and c[1].endswith("/releases/2") for c in api.calls))
        self.assertFalse(any(c[0] == "DELETE" and "/releases/assets/" in c[1] for c in api.calls))

    def test_missing_archive_or_pending_status_retries_the_same_source(self):
        for item in (dict(release(SHA), assets=release(SHA)["assets"][:1]),
                     dict(release(SHA), body=f"Upstream commit: {SHA}\nPublication status: pending")):
            self.assertTrue(needs_build(item, SHA))

    def test_stale_pending_replacement_forces_retry_without_pruning(self):
        api = FakeGitHub()
        api.current = release(SHA)
        api.items[4] = dict(release(OLD), id=4, tag_name=STAGING_PREFIX + 'stale', draft=True)
        self.assertEqual(run(api), (SHA, True))
        self.assertIn(4, api.items)
        self.assertFalse(any(c[0] == "DELETE" for c in api.calls))

    def test_second_upload_failure_keeps_old_release_and_retries_same_source(self):
        api = FakeGitHub()
        api.current = release(SHA)
        old = copy.deepcopy(api.current)
        def upload(path):
            if path.name == ARCHIVE:
                raise RuntimeError("archive upload failed")
            api.upload_asset(api.replacement, path)
        with self.assertRaisesRegex(RuntimeError, "archive upload failed"):
            self.publish_fixture(api, upload=upload)
        replacement_id = api.replacement["id"]
        self.assertEqual(api.current, old)
        self.assertTrue(api.replacement["draft"])
        self.assertEqual(run(api), (SHA, True))
        self.assertFalse(any(c[0] == "DELETE" for c in api.calls))
        self.publish_fixture(api)
        self.assertEqual(api.current["id"], replacement_id)
        self.assert_complete(api)

    def test_archive_confirmation_failure_preserves_old_release(self):
        api = FakeGitHub()
        old = copy.deepcopy(api.current)
        with self.assertRaisesRegex(RuntimeError, "was not confirmed"):
            self.publish_fixture(api, upload=lambda path: None)
        self.assertEqual(api.current, old)
        self.assertEqual(api.tag["object"]["sha"], OLD)
        self.assertFalse(any(c[0] == "DELETE" for c in api.calls))

    def test_new_publication_failure_preserves_old_release(self):
        api = FakeGitHub()
        old = copy.deepcopy(api.current)
        api.fail_publish = True
        with self.assertRaisesRegex(RuntimeError, "publication failed"):
            self.publish_fixture(api)
        self.assertEqual(api.current, old)
        self.assertFalse(any(c[0] == "DELETE" for c in api.calls))
        api.fail_publish = False
        self.publish_fixture(api)
        self.assert_complete(api)

    def test_cleanup_failure_reuses_published_replacement(self):
        api = FakeGitHub()
        api.fail_delete_id = 1
        with self.assertRaisesRegex(RuntimeError, "deletion failed"):
            self.publish_fixture(api)
        replacement_id = api.replacement["id"]
        self.assertFalse(api.replacement["draft"])
        self.assertEqual(api.current["id"], 1)
        api.fail_delete_id = None
        self.publish_fixture(api)
        self.assertEqual(api.current["id"], replacement_id)
        self.assert_complete(api)

    def test_unconfirmed_publication_never_deletes_old_release(self):
        api = FakeGitHub()
        old = copy.deepcopy(api.current)
        api.keep_draft_on_publish = True
        with self.assertRaisesRegex(RuntimeError, "still a draft"):
            self.publish_fixture(api)
        self.assertEqual(api.current, old)
        self.assertFalse(any(c[0] == "DELETE" for c in api.calls))

    def test_stable_tag_handover_failure_keeps_new_release_recoverable(self):
        api = FakeGitHub()
        api.fail_retag = True
        with self.assertRaisesRegex(RuntimeError, "handover failed"):
            self.publish_fixture(api)
        replacement_id = api.replacement["id"]
        self.assertIsNone(api.current)
        self.assertFalse(api.replacement["draft"])
        self.assertEqual(run(api), (SHA, True))
        api.fail_retag = False
        self.publish_fixture(api)
        self.assertEqual(api.current["id"], replacement_id)
        self.assert_complete(api)

    def test_tag_cleanup_failure_retries_pending_canonical_release(self):
        api = FakeGitHub()
        api.fail_tag_cleanup = True
        with self.assertRaisesRegex(RuntimeError, "Tag cleanup failed"):
            self.publish_fixture(api)
        replacement_id = api.current["id"]
        self.assertTrue(needs_build(api.current, SHA))
        self.assertEqual(run(api), (SHA, True))
        api.fail_tag_cleanup = False
        self.publish_fixture(api)
        self.assertEqual(api.current["id"], replacement_id)
        self.assert_complete(api)

    def test_forced_rebuild_creates_another_release_for_the_same_source(self):
        api = FakeGitHub()
        self.publish_fixture(api)
        first_id = api.current["id"]
        self.publish_fixture(api)
        self.assertNotEqual(api.current["id"], first_id)
        self.assert_complete(api)

    def test_unrelated_draft_is_retired_only_after_success(self):
        api = FakeGitHub()
        api.items[4] = dict(release(OLD), id=4, tag_name=STAGING_PREFIX + 'stale', draft=True)
        self.publish_fixture(api)
        self.assert_complete(api)

    def test_dangling_staging_tag_is_cleaned_after_stable_handover(self):
        api = FakeGitHub()
        for index in range(101):
            api.tags[STAGING_PREFIX + str(index)] = {"object": {"sha": OLD}}
        api.tags['unrelated'] = {"object": {"sha": OLD}}
        self.publish_fixture(api)
        self.assert_complete(api)
        matching_calls = [c for c in api.calls if "/matching-refs/" in c[1]]
        self.assertEqual(len(matching_calls), 1)
        self.assertNotIn("?", matching_calls[0][1])
        self.assertIn('unrelated', api.tags)

    def test_immutable_release_is_rejected_before_mutation(self):
        api = FakeGitHub()
        api.current['immutable'] = True
        with self.assertRaisesRegex(RuntimeError, "immutable"):
            self.publish_fixture(api)
        self.assertEqual(api.calls, [])

    def test_archive_must_match_before_any_remote_mutation(self):
        api = FakeGitHub()
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            create_archive(asset, EPOCH)
            asset.write_bytes(b"oops")
            with self.assertRaisesRegex(ValueError, "differs"):
                publish(api, asset, SHA, EPOCH)
        self.assertEqual(api.calls, [])

    def test_upload_failure_preserves_old_release_without_pruning(self):
        api = FakeGitHub()
        old = copy.deepcopy(api.current)
        def fail(_):
            raise RuntimeError("upload failed")
        with self.assertRaisesRegex(RuntimeError, "upload failed"):
            self.publish_fixture(api, upload=fail)
        self.assertEqual(api.current, old)
        self.assertTrue(api.replacement["draft"])
        self.assertFalse(any(c[0] == "DELETE" for c in api.calls))

    def test_first_publication_stays_draft_until_confirmed_upload(self):
        api = FakeGitHub(existing=False)
        def upload(path):
            self.assertIsNone(api.current)
            self.assertTrue(api.replacement["draft"])
            api.upload_asset(api.replacement, path)
        self.publish_fixture(api, upload=upload)
        self.assert_complete(api)

    def test_invalid_asset_never_touches_remote(self):
        api = FakeGitHub()
        with self.assertRaises(ValueError):
            publish(api, "/missing/wrong.mmdb", SHA, EPOCH)
        self.assertEqual(api.calls, [])

    def test_upload_targets_release_id_and_streams_asset(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', GH_TOKEN='test-token'):
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            create_archive(asset, EPOCH)
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
