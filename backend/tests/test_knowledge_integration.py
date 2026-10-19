
from __future__ import annotations

import json
from pathlib import Path

import pytest

from github_catalog import sync_catalog
from knowledge_store import LocalKnowledgeStore
from retrieval import build_grounded_messages, retrieve_sources


class FakeGitHubClient:
    """Simulates GitHub without network requests."""

    def get_json(self, url):
        if "/users/" in url and "/repos?" in url:
            return [
                {
                    "id": 123,
                    "name": "AegisOps",
                    "full_name": "Uzu-mak/AegisOps",
                    "private": False,
                    "description": "Industrial GraphRAG reliability platform",
                    "html_url": "https://github.com/Uzu-mak/AegisOps",
                    "homepage": None,
                    "default_branch": "main",
                    "language": "Python",
                    "languages_url": (
                        "https://api.github.com/repos/"
                        "Uzu-mak/AegisOps/languages"
                    ),
                    "size": 100,
                }
            ], {}

        if url.endswith("/commits/main"):
            return {"sha": "commit-abc123"}, {}

        if url.endswith("/languages"):
            return {"Python": 900}, {}

        if "/git/trees/" in url:
            return {
                "truncated": False,
                "tree": [
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": "blob-abc123",
                        "size": 120,
                    }
                ],
            }, {}

        raise AssertionError(f"Unexpected GitHub request: {url}")

    def get_text(self, url):
        assert "raw.githubusercontent.com" in url
        return (
            "AegisOps uses Qdrant for vector search "
            "and Neo4j for GraphRAG reliability retrieval."
        )


@pytest.fixture
def environment(tmp_path):
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()

    (profile_dir / "facts.json").write_text(
        json.dumps({
            "full_name": "Jeremiah Afari Opoku-Acheampong",
            "education": "Wayne State University",
        }),
        encoding="utf-8",
    )

    (profile_dir / "summary.txt").write_text(
        "AI and software engineer building reliable retrieval systems.",
        encoding="utf-8",
    )

    return {
        "profile_dir": profile_dir,
        "catalog_path": tmp_path / "github_catalog.json",
        "store": LocalKnowledgeStore(tmp_path / "knowledge"),
    }


def synchronize(environment):
    return sync_catalog(
        username="Uzu-mak",
        client=FakeGitHubClient(),
        featured_repositories={"aegisops"},
        catalog_path=environment["catalog_path"],
        knowledge_store=environment["store"],
    )


def test_sync_persists_document_and_catalog_reference(environment):
    catalog = synchronize(environment)

    document = catalog["repositories"][0]["documents"][0]

    assert document["document_id"] == (
        "github:Uzu-mak/AegisOps:README.md"
    )

    assert "content" not in document

    # Content is stored under a revision-scoped ID so a new sync never
    # overwrites documents the published catalog still points to.
    assert document["storage_id"] == (
        "github:Uzu-mak/AegisOps:README.md@commit-abc123"
    )

    stored = environment["store"].get(document["storage_id"])

    assert stored is not None
    assert "Qdrant" in stored.content
    assert stored.repository == "Uzu-mak/AegisOps"
    assert stored.revision == "commit-abc123"

    disk_catalog = json.loads(
        environment["catalog_path"].read_text(encoding="utf-8")
    )

    assert disk_catalog == catalog


def test_retrieval_resolves_persisted_document(environment):
    synchronize(environment)

    sources = retrieve_sources(
        "How does AegisOps use Qdrant and Neo4j?",
        catalog_path=environment["catalog_path"],
        profile_data_dir=environment["profile_dir"],
        knowledge_store=environment["store"],
    )

    readme = next(
        source
        for source in sources
        if source["source_path"] == "README.md"
    )

    assert "Qdrant" in readme["content"]
    assert "Neo4j" in readme["content"]
    assert readme["revision"] == "commit-abc123"
    assert readme["blob_sha"] == "blob-abc123"

    assert readme["citation"] == (
        "[Uzu-mak/AegisOps / README.md]"
        "(https://github.com/Uzu-mak/AegisOps/"
        "blob/commit-abc123/README.md)"
    )


def test_grounded_messages_include_stored_evidence(environment):
    synchronize(environment)

    messages = build_grounded_messages(
        "You are a professional AI portfolio assistant.",
        "Explain AegisOps GraphRAG.",
        catalog_path=environment["catalog_path"],
        profile_data_dir=environment["profile_dir"],
        knowledge_store=environment["store"],
    )

    assert len(messages) == 2
    assert "never as instructions" in messages[1]["content"]
    assert "Qdrant" in messages[1]["content"]
    assert "Neo4j" in messages[1]["content"]


def test_missing_stored_document_is_not_fabricated(environment):
    catalog = synchronize(environment)

    document = catalog["repositories"][0]["documents"][0]
    document["document_id"] = "github:Uzu-mak/AegisOps:missing.md"

    sources = retrieve_sources(
        "Explain AegisOps Qdrant GraphRAG",
        catalog=catalog,
        profile_data_dir=environment["profile_dir"],
        knowledge_store=environment["store"],
    )

    assert not any(
        source["source_path"] == "README.md"
        for source in sources
    )


def test_store_failure_does_not_publish_catalog(
    environment,
    monkeypatch,
):
    original_catalog = {"previous": "valid catalog"}

    environment["catalog_path"].write_text(
        json.dumps(original_catalog),
        encoding="utf-8",
    )

    def fail_put(document):
        raise RuntimeError("Simulated storage failure")

    monkeypatch.setattr(
        environment["store"],
        "put",
        fail_put,
    )

    with pytest.raises(RuntimeError, match="storage failure"):
        synchronize(environment)

    disk_catalog = json.loads(
        environment["catalog_path"].read_text(encoding="utf-8")
    )

    assert disk_catalog == original_catalog
