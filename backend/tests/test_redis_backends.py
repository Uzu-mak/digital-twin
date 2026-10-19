from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import github_catalog
import knowledge_store
from knowledge_store import (
    REDIS_KNOWLEDGE_INDEX_KEY,
    LocalKnowledgeStore,
    RedisKnowledgeStore,
    create_knowledge_store,
)
from storage import KnowledgeDocument

from fake_redis import FakeRedis


def make_document(document_id="github:Uzu-mak/AegisOps:README.md"):
    return KnowledgeDocument(
        document_id=document_id,
        source_url="https://github.com/Uzu-mak/AegisOps/blob/abc123/README.md",
        content="AegisOps is an industrial AI reliability platform.",
        repository="Uzu-mak/AegisOps",
        revision="abc123",
        updated_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
    )


class RedisKnowledgeStoreTests(unittest.TestCase):
    def test_put_get_and_list_ids(self):
        store = RedisKnowledgeStore(redis_url="", redis_client=FakeRedis())
        document = make_document()

        store.put(document)

        self.assertEqual(store.get(document.document_id), document)
        self.assertEqual(store.list_ids(), [document.document_id])

    def test_missing_document_returns_none(self):
        store = RedisKnowledgeStore(redis_url="", redis_client=FakeRedis())

        self.assertIsNone(store.get("github:Uzu-mak/missing:README.md"))

    def test_overwrite_keeps_single_index_entry(self):
        client = FakeRedis()
        store = RedisKnowledgeStore(redis_url="", redis_client=client)

        store.put(make_document())
        store.put(make_document())

        self.assertEqual(len(client.smembers(REDIS_KNOWLEDGE_INDEX_KEY)), 1)

    def test_delete_removes_document_and_index_entry(self):
        client = FakeRedis()
        store = RedisKnowledgeStore(redis_url="", redis_client=client)
        document = make_document()

        store.put(document)
        store.delete(document.document_id)
        store.delete(document.document_id)  # deleting twice is a no-op

        self.assertIsNone(store.get(document.document_id))
        self.assertEqual(store.list_ids(), [])

    def test_local_delete_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalKnowledgeStore(directory)
            document = make_document()

            store.put(document)
            store.delete(document.document_id)
            store.delete(document.document_id)

            self.assertIsNone(store.get(document.document_id))

    def test_requires_url_without_client(self):
        with self.assertRaises(ValueError):
            RedisKnowledgeStore(redis_url="")


class KnowledgeStoreFactoryTests(unittest.TestCase):
    def test_redis_backend_selected(self):
        with patch.dict(os.environ, {
            "KNOWLEDGE_STORE_BACKEND": "redis",
            "REDIS_URL": "redis://localhost:6379/0",
        }), patch.object(
            knowledge_store.RedisKnowledgeStore,
            "__init__",
            return_value=None,
        ):
            self.assertIsInstance(
                create_knowledge_store(),
                RedisKnowledgeStore,
            )

    def test_local_backend_selected(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"KNOWLEDGE_STORE_BACKEND": "local", "KNOWLEDGE_DIR": directory},
        ):
            self.assertIsInstance(
                create_knowledge_store(),
                LocalKnowledgeStore,
            )

    def test_unknown_backend_rejected(self):
        with patch.dict(os.environ, {"KNOWLEDGE_STORE_BACKEND": "dynamo"}):
            with self.assertRaises(ValueError):
                create_knowledge_store()


class FakeGitHubClient:
    def get_json(self, url):
        return [], {}


class InMemoryKnowledgeStore:
    def __init__(self) -> None:
        self.documents = {}

    def put(self, document):
        self.documents[document.document_id] = document


class CatalogBackendRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = FakeRedis()
        patcher = patch.object(
            github_catalog,
            "_redis_catalog_client",
            return_value=self.redis,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def sync(self, **kwargs):
        return github_catalog.sync_catalog(
            username="Uzu-mak",
            featured_repositories=set(),
            client=FakeGitHubClient(),
            knowledge_store=InMemoryKnowledgeStore(),
            **kwargs,
        )

    def test_redis_backend_without_path_publishes_to_redis(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"KNOWLEDGE_STORE_BACKEND": "redis"}
        ), patch.object(
            github_catalog,
            "CATALOG_PATH",
            Path(directory) / "github_catalog.json",
        ):
            self.sync()

            self.assertIn(github_catalog.REDIS_CATALOG_KEY, self.redis.values)
            self.assertFalse(github_catalog.CATALOG_PATH.exists())

            loaded = github_catalog.load_catalog()
            self.assertEqual(loaded["username"], "Uzu-mak")

    def test_explicit_path_stays_local_even_in_redis_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"KNOWLEDGE_STORE_BACKEND": "redis"}
        ):
            path = Path(directory) / "github_catalog.json"

            self.sync(catalog_path=path)

            self.assertTrue(path.is_file())
            self.assertNotIn(github_catalog.REDIS_CATALOG_KEY, self.redis.values)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["username"],
                "Uzu-mak",
            )

    def test_load_catalog_returns_empty_when_redis_has_no_catalog(self):
        with patch.dict(os.environ, {"KNOWLEDGE_STORE_BACKEND": "redis"}):
            catalog = github_catalog.load_catalog()

        self.assertEqual(catalog["repositories"], [])
        self.assertIsNone(catalog["last_synchronized_at"])


class RetrievalUsesConfiguredCatalogTests(unittest.TestCase):
    def test_default_retrieval_reads_redis_catalog(self):
        import retrieval

        redis = FakeRedis()
        redis.set(
            github_catalog.REDIS_CATALOG_KEY,
            json.dumps({
                "schema_version": 1,
                "username": "Uzu-mak",
                "repositories": [{
                    "name": "AegisOps",
                    "full_name": "Uzu-mak/AegisOps",
                    "description": "GraphRAG reliability platform",
                    "html_url": "https://github.com/Uzu-mak/AegisOps",
                    "is_featured": True,
                    "documents": [],
                }],
            }),
        )

        with patch.dict(
            os.environ, {"KNOWLEDGE_STORE_BACKEND": "redis"}
        ), patch.object(
            github_catalog,
            "_redis_catalog_client",
            return_value=redis,
        ):
            evidence = retrieval.retrieve_sources("Tell me about AegisOps")

        self.assertIn(
            "github:Uzu-mak/AegisOps:metadata",
            [item["source_id"] for item in evidence],
        )


if __name__ == "__main__":
    unittest.main()
