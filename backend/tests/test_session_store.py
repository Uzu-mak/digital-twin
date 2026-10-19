
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from session_store import (
    LocalSessionStore,
    deserialize,
    serialize,
    utc_now,
)
from storage import (
    ChatMessage,
    ChatSession,
    MAX_TURNS,
    SessionConflictError,
    validate_session_id,
)


# --------------------------------------------------
# Session creation and loading
# --------------------------------------------------

def test_create_and_load(tmp_path: Path):
    store = LocalSessionStore(tmp_path)
    session = store.create()

    loaded = store.load(session.session_id)

    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert loaded.messages == []
    assert loaded.version is not None


# --------------------------------------------------
# Conversation persistence
# --------------------------------------------------

def test_save_conversation(tmp_path: Path):
    store = LocalSessionStore(tmp_path)
    session = store.create()

    session.messages.append(
        ChatMessage(
            role="user",
            content="Tell me about AegisOps",
        )
    )

    store.save(session)

    loaded = store.load(session.session_id)

    assert loaded is not None
    assert len(loaded.messages) == 1
    assert loaded.messages[0].role == "user"
    assert loaded.messages[0].content == "Tell me about AegisOps"


def test_multiple_saves_refresh_version(tmp_path: Path):
    store = LocalSessionStore(tmp_path)
    session = store.create()

    original_version = session.version

    session.messages.append(
        ChatMessage(
            role="user",
            content="First message",
        )
    )

    store.save(session)

    first_saved_version = session.version

    assert first_saved_version is not None
    assert first_saved_version != original_version

    session.messages.append(
        ChatMessage(
            role="assistant",
            content="First response",
        )
    )

    store.save(session)

    loaded = store.load(session.session_id)

    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.version == session.version
    assert loaded.version != first_saved_version


# --------------------------------------------------
# Session ID validation
# --------------------------------------------------

def test_reject_invalid_session_id(tmp_path: Path):
    store = LocalSessionStore(tmp_path)

    with pytest.raises(ValueError):
        store.load("../../secrets")


def test_reject_non_uuid4_session_id():
    with pytest.raises(ValueError):
        validate_session_id(
            "00000000-0000-1000-8000-000000000001"
        )


def test_accept_valid_uuid4_session_id():
    session_id = str(uuid4())

    assert validate_session_id(session_id) == session_id


# --------------------------------------------------
# Unknown and expired sessions
# --------------------------------------------------

def test_reject_unknown_session(tmp_path: Path):
    store = LocalSessionStore(tmp_path)

    unknown_session = ChatSession(
        session_id=str(uuid4()),
        messages=[],
        updated_at=utc_now(),
        expires_at=utc_now() + timedelta(hours=1),
    )

    with pytest.raises(
        ValueError,
        match="Unknown or expired session",
    ):
        store.save(unknown_session)


def test_expired_session_is_unavailable(tmp_path: Path):
    store = LocalSessionStore(tmp_path)
    session = store.create()

    session.expires_at = utc_now() - timedelta(seconds=1)

    # Simulate an expired record already on disk.
    store._path(session.session_id).write_text(
        serialize(session),
        encoding="utf-8",
    )

    assert store.load(session.session_id) is None
    assert not store._path(session.session_id).exists()


def test_cannot_save_expired_session(tmp_path: Path):
    store = LocalSessionStore(tmp_path)
    session = store.create()

    session.expires_at = utc_now() - timedelta(seconds=1)

    with pytest.raises(
        ValueError,
        match="Expired session",
    ):
        store.save(session)


# --------------------------------------------------
# Optimistic concurrency control
# --------------------------------------------------

def test_detect_stale_version(tmp_path: Path):
    store = LocalSessionStore(tmp_path)
    session = store.create()

    first = store.load(session.session_id)
    second = store.load(session.session_id)

    assert first is not None
    assert second is not None

    first.messages.append(
        ChatMessage(
            role="user",
            content="First update",
        )
    )

    store.save(first)

    second.messages.append(
        ChatMessage(
            role="user",
            content="Stale update",
        )
    )

    with pytest.raises(SessionConflictError):
        store.save(second)


# --------------------------------------------------
# Conversation history limits
# --------------------------------------------------

def test_serialization_limits_history_to_max_turns():
    now = utc_now()

    session = ChatSession(
        session_id=str(uuid4()),
        messages=[
            ChatMessage(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
            )
            for i in range(60)
        ],
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )

    restored = deserialize(serialize(session))

    assert len(restored.messages) == MAX_TURNS * 2
    assert restored.messages[0].content == "Message 20"
    assert restored.messages[-1].content == "Message 59"


# --------------------------------------------------
# Session deletion
# --------------------------------------------------

def test_delete_session(tmp_path: Path):
    store = LocalSessionStore(tmp_path)
    session = store.create()

    store.delete(session.session_id)

    assert store.load(session.session_id) is None

    # Deleting an already-deleted session is harmless.
    store.delete(session.session_id)
