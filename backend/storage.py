
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


# Session configuration
SESSION_TTL_SECONDS = 3600  # 60 minutes
MAX_TURNS = 20

MessageRole = Literal["user", "assistant"]


@dataclass
class ChatMessage:
    role: MessageRole
    content: str


@dataclass
class ChatSession:
    session_id: str
    messages: list[ChatMessage]
    updated_at: datetime
    expires_at: datetime
    version: str | None = None


def validate_session_id(session_id: str) -> str:
    """Accept only canonical UUIDv4 session identifiers."""
    try:
        parsed = UUID(session_id)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid session ID") from None

    if parsed.version != 4 or str(parsed) != session_id:
        raise ValueError("Invalid session ID")

    return session_id


class SessionConflictError(Exception):
    """Raised when concurrent writes conflict."""


class SessionStore(ABC):

    @abstractmethod
    def create(self) -> ChatSession:
        """Create a new session."""

    @abstractmethod
    def load(self, session_id: str) -> ChatSession | None:
        """Load a valid, unexpired session."""

    @abstractmethod
    def save(self, session: ChatSession) -> None:
        """Save session changes with concurrency protection."""

    @abstractmethod
    def delete(self, session_id: str) -> None:
        """Delete a session."""


@dataclass
class KnowledgeDocument:
    document_id: str
    source_url: str
    content: str
    repository: str | None = None
    revision: str | None = None
    updated_at: datetime | None = None


class KnowledgeStore(ABC):

    @abstractmethod
    def put(self, document: KnowledgeDocument) -> None:
        """Store a knowledge document."""

    @abstractmethod
    def get(self, document_id: str) -> KnowledgeDocument | None:
        """Retrieve a knowledge document."""

    @abstractmethod
    def list_ids(self) -> list[str]:
        """List stored document identifiers."""
