
from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from session_store import (
    S3SessionStore,
    serialize,
    utc_now,
)
from storage import (
    ChatMessage,
    SessionConflictError,
)


@pytest.fixture
def s3():
    return MagicMock()


@pytest.fixture
def store(s3):
    return S3SessionStore(
        bucket="test-session-bucket",
        s3_client=s3,
    )


def s3_error(code):
    return ClientError(
        {
            "Error": {
                "Code": code,
                "Message": "Simulated S3 error",
            }
        },
        "PutObject",
    )


def test_create_captures_etag(store, s3):
    s3.put_object.return_value = {
        "ETag": '"etag-v1"',
    }

    session = store.create()

    assert session.version == '"etag-v1"'
    assert s3.put_object.call_args.kwargs["IfNoneMatch"] == "*"


def test_load_restores_etag(store, s3):
    s3.put_object.return_value = {
        "ETag": '"etag-v1"',
    }

    session = store.create()

    s3.get_object.return_value = {
        "Body": BytesIO(
            serialize(session).encode("utf-8")
        ),
        "ETag": '"etag-v1"',
    }

    loaded = store.load(session.session_id)

    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert loaded.version == '"etag-v1"'


def test_save_refreshes_etag(store, s3):
    s3.put_object.side_effect = [
        {"ETag": '"etag-v1"'},
        {"ETag": '"etag-v2"'},
    ]

    session = store.create()

    session.messages.append(
        ChatMessage(
            role="user",
            content="Hello",
        )
    )

    store.save(session)

    assert session.version == '"etag-v2"'
    assert s3.put_object.call_args.kwargs["IfMatch"] == '"etag-v1"'


def test_conflict_raises_session_conflict(store, s3):
    s3.put_object.side_effect = [
        {"ETag": '"etag-v1"'},
        s3_error("PreconditionFailed"),
    ]

    session = store.create()

    original_version = session.version
    original_updated_at = session.updated_at

    with pytest.raises(SessionConflictError):
        store.save(session)

    assert session.version == original_version
    assert session.updated_at == original_updated_at


def test_expired_session_cannot_be_saved(store, s3):
    s3.put_object.return_value = {
        "ETag": '"etag-v1"',
    }

    session = store.create()
    session.expires_at = utc_now() - timedelta(
        seconds=1
    )

    with pytest.raises(ValueError, match="Expired"):
        store.save(session)


def test_invalid_session_id_is_rejected(store):
    with pytest.raises(ValueError):
        store.load("../../secrets")


def test_delete_uses_expected_key(store, s3):
    s3.put_object.return_value = {
        "ETag": '"etag-v1"',
    }

    session = store.create()
    store.delete(session.session_id)

    s3.delete_object.assert_called_once_with(
        Bucket="test-session-bucket",
        Key=f"sessions/{session.session_id}.json",
    )
