
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from knowledge_store import (
    LocalKnowledgeStore,
    S3KnowledgeStore,
    document_key,
    serialize_document,
)
from storage import KnowledgeDocument


@pytest.fixture
def document():
    return KnowledgeDocument(
        document_id="github:Uzu-mak/AegisOps:README.md",
        source_url="https://github.com/Uzu-mak/AegisOps",
        content="AegisOps is an industrial AI reliability platform.",
        repository="Uzu-mak/AegisOps",
        revision="abc123",
        updated_at=datetime(
            2026, 10, 16, tzinfo=timezone.utc
        ),
    )


# --------------------------------------------------
# Local knowledge store tests
# --------------------------------------------------

def test_local_put_and_get(tmp_path, document):
    store = LocalKnowledgeStore(tmp_path)

    store.put(document)
    loaded = store.get(document.document_id)

    assert loaded == document


def test_local_missing_document(tmp_path):
    store = LocalKnowledgeStore(tmp_path)

    assert store.get("missing-document") is None


def test_local_list_ids(tmp_path, document):
    store = LocalKnowledgeStore(tmp_path)

    store.put(document)

    assert store.list_ids() == [document.document_id]


def test_local_overwrite_document(tmp_path, document):
    store = LocalKnowledgeStore(tmp_path)

    store.put(document)

    document.content = "Updated project documentation."
    store.put(document)

    loaded = store.get(document.document_id)

    assert loaded is not None
    assert loaded.content == "Updated project documentation."


def test_document_id_uses_safe_hash():
    key = document_key(
        "github:Uzu-mak/AegisOps:README.md"
    )

    assert key.startswith("documents/")
    assert key.endswith(".json")
    assert ".." not in key
    assert "/" not in key.removeprefix("documents/")


def test_reject_invalid_document_id(tmp_path):
    store = LocalKnowledgeStore(tmp_path)

    with pytest.raises(ValueError):
        store.get("")

    with pytest.raises(ValueError):
        store.get("invalid\nidentifier")


# --------------------------------------------------
# Mocked AWS S3 knowledge store tests
# --------------------------------------------------

@pytest.fixture
def s3():
    return MagicMock()


@pytest.fixture
def s3_store(s3):
    return S3KnowledgeStore(
        bucket="test-knowledge-bucket",
        s3_client=s3,
    )


def test_s3_put(s3_store, s3, document):
    s3_store.put(document)

    s3.put_object.assert_called_once_with(
        Bucket="test-knowledge-bucket",
        Key=document_key(document.document_id),
        Body=serialize_document(document).encode("utf-8"),
        ContentType="application/json",
    )


def test_s3_get(s3_store, s3, document):
    s3.get_object.return_value = {
        "Body": BytesIO(
            serialize_document(document).encode("utf-8")
        )
    }

    loaded = s3_store.get(document.document_id)

    assert loaded == document


def test_s3_missing_document(s3_store, s3):
    s3.get_object.side_effect = ClientError(
        {
            "Error": {
                "Code": "NoSuchKey",
                "Message": "Not found",
            }
        },
        "GetObject",
    )

    assert s3_store.get("missing-document") is None


def test_s3_list_ids(s3_store, s3, document):
    paginator = MagicMock()

    paginator.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": document_key(
                        document.document_id
                    )
                }
            ]
        }
    ]

    s3.get_paginator.return_value = paginator

    s3.get_object.return_value = {
        "Body": BytesIO(
            serialize_document(document).encode("utf-8")
        )
    }

    ids = s3_store.list_ids()

    assert ids == [document.document_id]

    s3.get_paginator.assert_called_once_with(
        "list_objects_v2"
    )

    paginator.paginate.assert_called_once_with(
        Bucket="test-knowledge-bucket",
        Prefix="documents/",
    )


def test_s3_rejects_document_id_mismatch(
    s3_store,
    s3,
    document,
):
    s3.get_object.return_value = {
        "Body": BytesIO(
            serialize_document(document).encode("utf-8")
        )
    }

    with pytest.raises(
        ValueError,
        match="Knowledge document ID mismatch",
    ):
        s3_store.get("different-document")
