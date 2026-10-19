
from datetime import datetime, timezone

import pytest

from knowledge_store import RedisKnowledgeStore
from storage import KnowledgeDocument


class FakeRedis:
    """Minimal in-memory Redis replacement for unit tests."""

    def __init__(self):
        self.data = {}

    def set(self, key, value):
        self.data[key] = value
        return True

    def get(self, key):
        return self.data.get(key)

    def scan_iter(self, match=None, count=None):
        prefix = match[:-1] if match.endswith("*") else match

        for key in list(self.data):
            if key.startswith(prefix):
                yield key


@pytest.fixture
def redis_store():
    client = FakeRedis()
    return RedisKnowledgeStore(redis_client=client)


@pytest.fixture
def sample_document():
    return KnowledgeDocument(
        document_id="github:Uzu-mak/AegisOps:README.md",
        source_url=(
            "https://github.com/Uzu-mak/AegisOps/"
            "blob/abc123/README.md"
        ),
        content="# AegisOps\nIndustrial fault diagnosis.",
        repository="Uzu-mak/AegisOps",
        revision="abc123",
        updated_at=datetime.now(timezone.utc),
    )


def test_put_and_get(redis_store, sample_document):
    redis_store.put(sample_document)

    retrieved = redis_store.get(sample_document.document_id)

    assert retrieved is not None
    assert retrieved.document_id == sample_document.document_id
    assert retrieved.content == sample_document.content
    assert retrieved.repository == sample_document.repository
    assert retrieved.revision == sample_document.revision
    assert retrieved.source_url == sample_document.source_url
    assert retrieved.updated_at == sample_document.updated_at


def test_missing_document(redis_store):
    assert redis_store.get("github:missing:README.md") is None


def test_list_ids(redis_store, sample_document):
    redis_store.put(sample_document)

    second = KnowledgeDocument(
        document_id="github:Uzu-mak/CareerOps:README.md",
        source_url="https://github.com/Uzu-mak/CareerOps",
        content="# CareerOps",
    )

    redis_store.put(second)

    assert redis_store.list_ids() == sorted([
        sample_document.document_id,
        second.document_id,
    ])


def test_session_keys_are_untouched(redis_store, sample_document):
    session_key = "digital-twin:sessions:example-session"
    redis_store.client.set(session_key, "session-data")

    redis_store.put(sample_document)

    assert redis_store.client.get(session_key) == "session-data"
    assert redis_store.list_ids() == [sample_document.document_id]


def test_invalid_document_id(redis_store):
    with pytest.raises(ValueError):
        redis_store.get("invalid\nid")


def test_no_expiration_used(redis_store, sample_document):
    redis_store.put(sample_document)

    assert len(redis_store.client.data) == 1
    assert redis_store.get(sample_document.document_id) is not None


from knowledge_store import (
    LocalKnowledgeStore,
    RedisKnowledgeStore,
    create_knowledge_store,
)


def test_factory_defaults_to_local(monkeypatch, tmp_path):
    monkeypatch.delenv("KNOWLEDGE_STORE_BACKEND", raising=False)
    monkeypatch.setenv("USE_S3", "false")
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))

    store = create_knowledge_store()

    assert isinstance(store, LocalKnowledgeStore)


def test_factory_selects_redis(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_STORE_BACKEND", "redis")
    monkeypatch.setenv(
        "REDIS_URL",
        "redis://localhost:6379/0",
    )

    store = create_knowledge_store()

    assert isinstance(store, RedisKnowledgeStore)


def test_factory_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv(
        "KNOWLEDGE_STORE_BACKEND",
        "unknown",
    )

    with pytest.raises(ValueError, match="Unsupported"):
        create_knowledge_store()


