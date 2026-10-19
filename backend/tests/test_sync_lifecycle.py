"""End-to-end synchronization lifecycle in Redis mode, with GitHub mocked.

Covers new/updated/renamed/deleted/private repositories, failure
preservation, concurrency, stale-document cleanup, rollback and the
retrieval path, all without a local catalog file.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import github_catalog
import retrieval
import sync_github
from github_catalog import (
    REDIS_CATALOG_KEY,
    REDIS_PREVIOUS_CATALOG_KEY,
    REDIS_SYNC_LOCK_KEY,
    GitHubRateLimitError,
    GitHubStorageError,
    GitHubSyncError,
    GitHubSyncInProgressError,
    load_catalog,
    load_previous_catalog,
    public_directory,
    rollback_catalog,
    sync_catalog,
)
from knowledge_store import RedisKnowledgeStore

from fake_redis import FakeRedis


OWNER = "Uzu-mak"


class FakeGitHub:
    """Mutable fake of the GitHub REST API and raw file host."""

    def __init__(self) -> None:
        self.repos: dict[str, dict] = {}
        self.fail_commit_for: set[str] = set()
        self.rate_limit_on_listing = False
        self.page_size = 100

    def add(self, name, readme="", sha="sha-1", private=False, size=10,
            docs=None, branch="main"):
        files = {"README.md": readme} if readme else {}
        files.update(docs or {})
        self.repos[name] = {
            "files": files, "sha": sha, "private": private,
            "size": size, "branch": branch,
        }

    def _repo_payload(self, name, repo):
        return {
            "id": abs(hash(name)) % 10_000,
            "name": name,
            "full_name": f"{OWNER}/{name}",
            "private": repo["private"],
            "description": f"{name} description",
            "html_url": f"https://github.com/{OWNER}/{name}",
            "homepage": None,
            "default_branch": repo["branch"],
            "language": "Python",
            "languages_url": (
                f"https://api.github.com/repos/{OWNER}/{name}/languages"
            ),
            "size": repo["size"],
        }

    def get_json(self, url):
        if f"/users/{OWNER}/repos" in url:
            if self.rate_limit_on_listing:
                raise GitHubRateLimitError("GitHub API rate limit reached.")

            names = sorted(self.repos)
            page = int(url.split("page=")[-1]) if "&page=" in url else 1
            chunk = names[(page - 1) * self.page_size: page * self.page_size]
            headers = {}

            if page * self.page_size < len(names):
                headers["link"] = (
                    f'<https://api.github.com/users/{OWNER}/repos'
                    f'?per_page={self.page_size}&page={page + 1}>; rel="next"'
                )

            return [
                self._repo_payload(name, self.repos[name]) for name in chunk
            ], headers

        name = url.split(f"/repos/{OWNER}/")[1].split("/")[0]
        repo = self.repos[name]

        if "/commits/" in url:
            if name in self.fail_commit_for:
                raise GitHubSyncError("GitHub request failed with HTTP 502.")
            return {"sha": repo["sha"]}, {}

        if url.endswith("/languages"):
            return {"Python": 100}, {}

        if "/git/trees/" in url:
            return {
                "truncated": False,
                "tree": [
                    {"path": path, "type": "blob", "sha": f"blob-{path}",
                     "size": len(text)}
                    for path, text in repo["files"].items()
                ],
            }, {}

        raise AssertionError(f"Unexpected request {url}")

    def get_text(self, url):
        _, _, rest = url.partition(f"raw.githubusercontent.com/{OWNER}/")
        name, sha, path = rest.split("/", 2)
        repo = self.repos[name]
        assert sha == repo["sha"], "raw file requested at the wrong commit"
        return repo["files"][path]


class RedisModeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = FakeRedis()
        self.github = FakeGitHub()
        self.store = RedisKnowledgeStore(redis_url="", redis_client=self.redis)

        # A local catalog path that must never be created in Redis mode.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.local_catalog = Path(self.tmp.name) / "github_catalog.json"

        for patcher in (
            patch.dict(os.environ, {
                "KNOWLEDGE_STORE_BACKEND": "redis",
                "REDIS_URL": "redis://fake",
                "GITHUB_USERNAME": OWNER,
            }),
            patch.object(github_catalog, "_redis_catalog_client",
                         return_value=self.redis),
            patch.object(github_catalog, "CATALOG_PATH", self.local_catalog),
            patch.object(github_catalog, "create_knowledge_store",
                         return_value=self.store),
            patch.object(retrieval, "create_knowledge_store",
                         return_value=self.store),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def sync(self, **kwargs):
        return sync_catalog(
            username=OWNER,
            featured_repositories={"aegisops"},
            client=self.github,
            **kwargs,
        )

    def published_names(self):
        return sorted(
            repo["name"] for repo in public_directory()["repositories"]
        )

    def evidence(self, query):
        return retrieval.retrieve_sources(query)

    def readme_sources(self, query):
        return [
            item for item in self.evidence(query)
            if item.get("source_path") == "README.md"
        ]


class SyncLifecycleTests(RedisModeTestCase):

    def test_redis_mode_needs_no_local_catalog_file(self):
        self.github.add("AegisOps", readme="AegisOps uses Qdrant.")

        self.sync()

        self.assertIn(REDIS_CATALOG_KEY, self.redis.values)
        self.assertFalse(self.local_catalog.exists())
        self.assertEqual(self.published_names(), ["AegisOps"])

    def test_discovery_follows_pagination(self):
        self.github.page_size = 2
        for index in range(5):
            self.github.add(f"repo-{index}", readme=f"Repo {index}")

        catalog = self.sync()

        self.assertEqual(catalog["repository_count"], 5)
        self.assertEqual(len(self.published_names()), 5)

    def test_new_repository_appears_after_next_sync(self):
        self.github.add("AegisOps", readme="AegisOps")
        self.sync()

        self.github.add("NewProject", readme="NewProject uses LangGraph.")
        self.sync()

        self.assertEqual(self.published_names(), ["AegisOps", "NewProject"])
        self.assertTrue(self.readme_sources("NewProject LangGraph"))

    def test_readme_update_propagates_and_old_text_is_not_retrieved(self):
        self.github.add("AegisOps", readme="AegisOps uses Pinecone.", sha="sha-1")
        self.sync()

        self.github.add("AegisOps", readme="AegisOps uses Qdrant.", sha="sha-2")
        self.sync()

        sources = self.readme_sources("AegisOps Qdrant Pinecone")
        self.assertEqual(len(sources), 1)
        self.assertIn("Qdrant", sources[0]["content"])
        self.assertNotIn("Pinecone", sources[0]["content"])
        self.assertEqual(sources[0]["revision"], "sha-2")
        self.assertIn("/blob/sha-2/README.md", sources[0]["citation"])

    def test_deleted_and_private_repositories_are_removed(self):
        self.github.add("Keep", readme="Keep project")
        self.github.add("Deleted", readme="Deleted project secret sauce")
        self.github.add("GoingPrivate", readme="GoingPrivate details")
        self.sync()

        del self.github.repos["Deleted"]
        self.github.repos["GoingPrivate"]["private"] = True
        self.sync()

        self.assertEqual(self.published_names(), ["Keep"])
        self.assertFalse(self.readme_sources("Deleted secret sauce"))
        self.assertFalse(self.readme_sources("GoingPrivate details"))

    def test_renamed_repository_uses_new_name_and_links(self):
        self.github.add("OldName", readme="Renamed project text")
        self.sync()

        self.github.repos["NewName"] = self.github.repos.pop("OldName")
        self.sync()

        self.assertEqual(self.published_names(), ["NewName"])
        citations = [s["citation"] for s in self.readme_sources("Renamed project")]
        self.assertEqual(len(citations), 1)
        self.assertIn(f"github.com/{OWNER}/NewName/blob/", citations[0])

    def test_empty_and_readme_less_repositories_are_listed(self):
        self.github.add("Empty", size=0)
        self.github.add("NoReadme", readme="")

        catalog = self.sync()

        self.assertEqual(self.published_names(), ["Empty", "NoReadme"])
        for repository in catalog["repositories"]:
            self.assertEqual(repository["documents"], [])

    def test_stale_documents_are_deleted_after_snapshot_rotates(self):
        self.github.add("Gone", readme="Gone text")
        self.sync()

        del self.github.repos["Gone"]
        self.github.add("Other", readme="Other text")
        self.sync()
        # The previous snapshot still references Gone, so it is kept for
        # rollback until one more successful sync.
        self.assertTrue(any("Gone" in i for i in self.store.list_ids()))

        self.sync()
        self.assertFalse(any("Gone" in i for i in self.store.list_ids()))

    def test_unchanged_repository_sync_is_idempotent(self):
        self.github.add("AegisOps", readme="AegisOps")
        first = self.sync()
        second = self.sync()

        self.assertEqual(
            first["repositories"][0]["documents"][0]["storage_id"],
            second["repositories"][0]["documents"][0]["storage_id"],
        )
        self.assertEqual(len(self.store.list_ids()), 1)


class FailurePreservationTests(RedisModeTestCase):

    def setUp(self) -> None:
        super().setUp()
        self.github.add("AegisOps", readme="AegisOps uses Qdrant.", sha="sha-1")
        self.good = self.sync()

    def assert_catalog_unchanged(self):
        self.assertEqual(load_catalog(), self.good)
        self.assertTrue(self.readme_sources("AegisOps Qdrant"))

    def test_rate_limit_preserves_published_snapshot(self):
        self.github.rate_limit_on_listing = True

        with self.assertRaises(GitHubRateLimitError):
            self.sync()

        self.assert_catalog_unchanged()

    def test_storage_failure_mid_sync_keeps_old_catalog_and_documents(self):
        self.github.add("AegisOps", readme="AegisOps uses Weaviate.", sha="sha-2")

        with patch.object(self.store, "put", side_effect=ConnectionError("down")):
            with self.assertRaises(ConnectionError):
                self.sync()

        self.assert_catalog_unchanged()
        self.assertIn("Qdrant", self.readme_sources("AegisOps")[0]["content"])

    def test_documents_written_before_a_failed_publish_do_not_leak(self):
        self.github.add("AegisOps", readme="AegisOps uses Weaviate.", sha="sha-2")

        with patch.object(
            github_catalog, "_write_redis_catalog",
            side_effect=GitHubStorageError("publish failed"),
        ):
            with self.assertRaises(GitHubStorageError):
                self.sync()

        # The new revision's document exists but is not referenced.
        self.assertIn("Qdrant", self.readme_sources("AegisOps")[0]["content"])

    def test_transient_repo_failure_keeps_previous_documentation(self):
        self.github.fail_commit_for.add("AegisOps")

        catalog = self.sync()

        documents = catalog["repositories"][0]["documents"]
        self.assertEqual(documents[0]["storage_id"],
                         self.good["repositories"][0]["documents"][0]["storage_id"])
        self.assertTrue(any("kept documentation" in w for w in catalog["warnings"]))
        self.assertTrue(self.readme_sources("AegisOps Qdrant"))

    def test_empty_listing_does_not_wipe_catalog_without_override(self):
        self.github.repos.clear()

        with self.assertRaises(GitHubSyncError):
            self.sync()
        self.assert_catalog_unchanged()

        catalog = self.sync(allow_empty=True)
        self.assertEqual(catalog["repositories"], [])

    def test_concurrent_sync_is_rejected_and_lock_released_after(self):
        self.redis.set(REDIS_SYNC_LOCK_KEY, "other-run")

        with self.assertRaises(GitHubSyncInProgressError):
            self.sync()
        self.assert_catalog_unchanged()

        self.redis.delete(REDIS_SYNC_LOCK_KEY)
        self.sync()
        self.assertNotIn(REDIS_SYNC_LOCK_KEY, self.redis.values)

    def test_lock_is_released_when_sync_fails(self):
        self.github.rate_limit_on_listing = True

        with self.assertRaises(GitHubRateLimitError):
            self.sync()

        self.assertNotIn(REDIS_SYNC_LOCK_KEY, self.redis.values)

    def test_rollback_restores_previous_snapshot_and_its_documents(self):
        self.github.add("AegisOps", readme="AegisOps uses Weaviate.", sha="sha-2")
        self.sync()
        self.assertIn("Weaviate", self.readme_sources("AegisOps")[0]["content"])

        restored = rollback_catalog()

        self.assertEqual(restored, self.good)
        self.assertEqual(load_catalog(), self.good)
        self.assertIn("Qdrant", self.readme_sources("AegisOps")[0]["content"])

        # Rolling back again returns to the newer snapshot.
        rollback_catalog()
        self.assertIn("Weaviate", self.readme_sources("AegisOps")[0]["content"])


class StorageErrorTests(RedisModeTestCase):

    def test_missing_redis_catalog_is_empty(self):
        catalog = load_catalog()
        self.assertEqual(catalog["repositories"], [])
        self.assertIsNone(load_previous_catalog())

    def test_corrupt_redis_catalog_raises_clear_error(self):
        self.redis.set(REDIS_CATALOG_KEY, "{not json")

        with self.assertRaisesRegex(GitHubSyncError, "invalid"):
            load_catalog()

        self.redis.set(REDIS_CATALOG_KEY, json.dumps({"repositories": [1]}))

        with self.assertRaisesRegex(GitHubSyncError, "invalid"):
            load_catalog()

    def test_sync_repairs_a_corrupt_catalog(self):
        self.redis.set(REDIS_CATALOG_KEY, "{not json")
        self.github.add("AegisOps", readme="AegisOps")

        self.sync()

        self.assertEqual(self.published_names(), ["AegisOps"])

    def test_redis_outage_is_an_error_not_an_empty_catalog(self):
        self.redis.fail = True

        with self.assertRaises(GitHubStorageError):
            load_catalog()

        with self.assertRaises(GitHubStorageError):
            public_directory()

        with self.assertRaises(GitHubStorageError):
            self.sync()

    def test_chat_retrieval_degrades_to_profile_during_outage(self):
        self.redis.fail = True

        evidence = self.evidence("Tell me about AegisOps")

        self.assertTrue(evidence)
        self.assertTrue(all(
            item["source_id"].startswith("profile:") for item in evidence
        ))

    def test_previous_snapshot_key_is_written_on_publish(self):
        self.github.add("AegisOps", readme="v1", sha="sha-1")
        self.sync()
        self.github.add("AegisOps", readme="v2", sha="sha-2")
        self.sync()

        previous = json.loads(self.redis.values[REDIS_PREVIOUS_CATALOG_KEY])
        self.assertEqual(
            previous["repositories"][0]["last_commit_sha"], "sha-1"
        )


class CommandLineTests(RedisModeTestCase):

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()

        with patch.object(github_catalog, "GitHubClient",
                          return_value=self.github), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = sync_github.main(list(args))

        return code, stdout.getvalue(), stderr.getvalue()

    def test_sync_status_and_rollback_commands(self):
        self.github.add("AegisOps", readme="AegisOps")

        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("to Redis", out)

        code, out, _ = self.run_cli("--status")
        self.assertEqual(code, 0)
        self.assertIn("Published: synchronized", out)
        self.assertIn("1 repositories", out)

        code, _, err = self.run_cli("--rollback")
        self.assertEqual(code, 1)
        self.assertIn("No previous catalog", err)

    def test_failed_sync_exits_nonzero_without_secrets_in_output(self):
        self.github.rate_limit_on_listing = True

        code, out, err = self.run_cli()

        self.assertEqual(code, 1)
        self.assertIn("rate limit", err)
        self.assertNotIn("redis://", out + err)

    def test_explicit_catalog_path_writes_local_file(self):
        self.github.add("AegisOps", readme="AegisOps")
        path = Path(self.tmp.name) / "explicit.json"

        code, out, _ = self.run_cli("--catalog", str(path))

        self.assertEqual(code, 0)
        self.assertTrue(path.is_file())
        self.assertNotIn(REDIS_CATALOG_KEY, self.redis.values)


class LocalModeTests(unittest.TestCase):
    """Development mode keeps the same snapshot and cleanup semantics."""

    def test_local_snapshot_rollback_and_cleanup(self):
        from knowledge_store import LocalKnowledgeStore

        github = FakeGitHub()

        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"KNOWLEDGE_STORE_BACKEND": "local"}):
            path = Path(directory) / "catalog.json"
            store = LocalKnowledgeStore(Path(directory) / "knowledge")

            def sync():
                return sync_catalog(
                    username=OWNER, featured_repositories=set(),
                    client=github, catalog_path=path, knowledge_store=store,
                )

            github.add("AegisOps", readme="v1", sha="sha-1")
            first = sync()
            github.add("AegisOps", readme="v2", sha="sha-2")
            sync()
            github.add("AegisOps", readme="v3", sha="sha-3")
            sync()

            ids = store.list_ids()
            self.assertEqual(sorted(i.rsplit("@", 1)[1] for i in ids),
                             ["sha-2", "sha-3"])

            restored = rollback_catalog(path)
            self.assertEqual(
                restored["repositories"][0]["last_commit_sha"], "sha-2"
            )
            self.assertNotEqual(restored, first)


if __name__ == "__main__":
    unittest.main()
