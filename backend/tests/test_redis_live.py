"""Optional sync lifecycle test against a real Redis instance.

Skipped unless DIGITAL_TWIN_TEST_REDIS_URL is set. Point it at a separate,
empty test database (for example a second Upstash database): the test
writes and then deletes the same catalog keys production uses.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

TEST_REDIS_URL = os.getenv("DIGITAL_TWIN_TEST_REDIS_URL", "").strip()


@unittest.skipUnless(TEST_REDIS_URL, "DIGITAL_TWIN_TEST_REDIS_URL not set")
class LiveRedisSyncTests(unittest.TestCase):

    def setUp(self) -> None:
        if TEST_REDIS_URL == os.getenv("REDIS_URL", "").strip():
            self.skipTest("Refusing to run against the production REDIS_URL.")

        import redis

        import github_catalog
        import retrieval
        from knowledge_store import RedisKnowledgeStore
        from test_sync_lifecycle import FakeGitHub

        self.client = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
        self.store = RedisKnowledgeStore(redis_url=TEST_REDIS_URL)
        self.github = FakeGitHub()

        for patcher in (
            patch.dict(os.environ, {
                "KNOWLEDGE_STORE_BACKEND": "redis",
                "REDIS_URL": TEST_REDIS_URL,
            }),
            patch.object(github_catalog, "create_knowledge_store",
                         return_value=self.store),
            patch.object(retrieval, "create_knowledge_store",
                         return_value=self.store),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import github_catalog

        for document_id in self.store.list_ids():
            self.store.delete(document_id)

        self.client.delete(
            github_catalog.REDIS_CATALOG_KEY,
            github_catalog.REDIS_PREVIOUS_CATALOG_KEY,
            github_catalog.REDIS_SYNC_LOCK_KEY,
        )

    def test_sync_publish_retrieve_update_and_rollback(self):
        import github_catalog
        import retrieval

        def sync():
            return github_catalog.sync_catalog(
                username="Uzu-mak", featured_repositories=set(),
                client=self.github,
            )

        self.github.add("LiveRepo", readme="LiveRepo uses Qdrant.", sha="sha-1")
        sync()
        self.github.add("LiveRepo", readme="LiveRepo uses Neo4j.", sha="sha-2")
        sync()

        def readme():
            return [
                item["content"]
                for item in retrieval.retrieve_sources("LiveRepo Qdrant Neo4j")
                if item.get("source_path") == "README.md"
            ]

        self.assertIn("Neo4j", readme()[0])
        github_catalog.rollback_catalog()
        self.assertIn("Qdrant", readme()[0])
        self.assertIsNone(self.client.get(github_catalog.REDIS_SYNC_LOCK_KEY))


if __name__ == "__main__":
    unittest.main()
