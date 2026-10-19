
import hashlib
import json
import os
import uuid

from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage import (
    ChatMessage,
    ChatSession,
    SessionStore,
    SessionConflictError,
    MAX_TURNS,
    SESSION_TTL_SECONDS,
    validate_session_id,
)


# --------------------------------------------------
# Shared session utilities
# --------------------------------------------------

def utc_now():
    return datetime.now(timezone.utc)


def new_session():
    now = utc_now()

    return ChatSession(
        session_id=str(uuid.uuid4()),
        messages=[],
        updated_at=now,
        expires_at=now + timedelta(
            seconds=SESSION_TTL_SECONDS
        ),
    )


def serialize(session):
    return json.dumps({
        "session_id": session.session_id,
        "messages": [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in session.messages[-MAX_TURNS * 2:]
        ],
        "updated_at": session.updated_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
    })


def deserialize(data, version=None):
    obj = json.loads(data)

    return ChatSession(
        session_id=validate_session_id(obj["session_id"]),
        messages=[
            ChatMessage(**message)
            for message in obj["messages"]
        ],
        updated_at=datetime.fromisoformat(
            obj["updated_at"]
        ),
        expires_at=datetime.fromisoformat(
            obj["expires_at"]
        ),
        version=version,
    )


def expired(session):
    return utc_now() >= session.expires_at


# --------------------------------------------------
# Local session storage
# --------------------------------------------------

class LocalSessionStore(SessionStore):

    def __init__(self, directory="../memory"):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    def _path(self, session_id):
        validate_session_id(session_id)
        return self.directory / f"{session_id}.json"

    def _version(self, data):
        return hashlib.sha256(
            data.encode("utf-8")
        ).hexdigest()

    def create(self):
        session = new_session()
        path = self._path(session.session_id)
        data = serialize(session)

        with path.open("x", encoding="utf-8") as file:
            file.write(data)

        session.version = self._version(data)
        return session

    def load(self, session_id):
        path = self._path(session_id)

        if not path.is_file() or path.is_symlink():
            return None

        data = path.read_text(encoding="utf-8")

        session = deserialize(
            data,
            version=self._version(data),
        )

        if expired(session):
            path.unlink(missing_ok=True)
            return None

        return session

    def save(self, session):
        path = self._path(session.session_id)

        if expired(session):
            raise ValueError("Expired session")

        current = self.load(session.session_id)

        if current is None:
            raise ValueError(
                "Unknown or expired session"
            )

        if current.version != session.version:
            raise SessionConflictError(
                "Session version conflict"
            )

        now = utc_now()

        session.updated_at = now
        session.expires_at = now + timedelta(
            seconds=SESSION_TTL_SECONDS
        )

        data = serialize(session)

        temporary_path = path.with_name(
            f".{path.stem}.{uuid.uuid4().hex}.tmp"
        )

        try:
            with temporary_path.open(
                "x",
                encoding="utf-8",
            ) as file:
                file.write(data)

            os.replace(
                temporary_path,
                path,
            )

        finally:
            temporary_path.unlink(
                missing_ok=True
            )

        session.version = self._version(data)

    def delete(self, session_id):
        self._path(session_id).unlink(
            missing_ok=True
        )


# --------------------------------------------------
# AWS S3 session storage
# --------------------------------------------------

class S3SessionStore(SessionStore):

    def __init__(self, bucket, s3_client=None):
        if not bucket:
            raise ValueError(
                "S3 bucket is required"
            )

        if s3_client is None:
            import boto3
            s3_client = boto3.client("s3")

        self.s3 = s3_client
        self.bucket = bucket

    def _key(self, session_id):
        validate_session_id(session_id)
        return f"sessions/{session_id}.json"

    def create(self):
        from botocore.exceptions import ClientError

        session = new_session()

        try:
            response = self.s3.put_object(
                Bucket=self.bucket,
                Key=self._key(session.session_id),
                Body=serialize(session).encode("utf-8"),
                ContentType="application/json",
                IfNoneMatch="*",
            )

        except ClientError as exc:
            error_code = exc.response[
                "Error"
            ]["Code"]

            if error_code in (
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "412",
                "409",
            ):
                raise SessionConflictError(
                    "Session creation conflict"
                ) from exc

            raise

        session.version = response["ETag"]

        return session

    def load(self, session_id):
        from botocore.exceptions import ClientError

        try:
            response = self.s3.get_object(
                Bucket=self.bucket,
                Key=self._key(session_id),
            )

        except ClientError as exc:
            error_code = exc.response[
                "Error"
            ]["Code"]

            if error_code in (
                "NoSuchKey",
                "404",
                "NotFound",
            ):
                return None

            raise

        session = deserialize(
            response["Body"].read().decode("utf-8"),
            version=response["ETag"],
        )

        if expired(session):
            return None

        return session

    def save(self, session):
        from botocore.exceptions import ClientError

        if session.version is None:
            raise ValueError(
                "Session version is required"
            )

        if expired(session):
            raise ValueError(
                "Expired session"
            )

        now = utc_now()

        new_expires_at = now + timedelta(
            seconds=SESSION_TTL_SECONDS
        )

        updated_session = ChatSession(
            session_id=session.session_id,
            messages=session.messages,
            updated_at=now,
            expires_at=new_expires_at,
            version=session.version,
        )

        try:
            response = self.s3.put_object(
                Bucket=self.bucket,
                Key=self._key(session.session_id),
                Body=serialize(updated_session).encode("utf-8"),
                ContentType="application/json",
                IfMatch=session.version,
            )

        except ClientError as exc:
            error_code = exc.response[
                "Error"
            ]["Code"]

            if error_code in (
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "412",
                "409",
            ):
                raise SessionConflictError(
                    "Session was modified concurrently"
                ) from exc

            raise

        session.updated_at = now
        session.expires_at = new_expires_at
        session.version = response["ETag"]

    def delete(self, session_id):
        self.s3.delete_object(
            Bucket=self.bucket,
            Key=self._key(session_id),
        )


# --------------------------------------------------
# Redis session storage (Vercel / Upstash)
# --------------------------------------------------

class RedisSessionStore(SessionStore):

    # Atomically compare the current serialized value
    # with the expected value before saving.
    _SAVE_SCRIPT = """
    local current = redis.call('GET', KEYS[1])

    if not current then
        return 0
    end

    if current ~= ARGV[1] then
        return -1
    end

    redis.call(
        'SET',
        KEYS[1],
        ARGV[2],
        'EX',
        ARGV[3]
    )

    return 1
    """

    # Remove an expired value only if it has not
    # changed since it was read.
    _DELETE_IF_UNCHANGED_SCRIPT = """
    local current = redis.call('GET', KEYS[1])

    if current and current == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    end

    return 0
    """

    def __init__(self, redis_url, redis_client=None):
        if redis_client is None:
            if not redis_url:
                raise ValueError(
                    "Redis URL is required"
                )

            import redis

            redis_client = redis.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )

        self.client = redis_client

        self.save_script = self.client.register_script(
            self._SAVE_SCRIPT
        )

        self.delete_if_unchanged_script = (
            self.client.register_script(
                self._DELETE_IF_UNCHANGED_SCRIPT
            )
        )

    def _key(self, session_id):
        validate_session_id(session_id)

        return f"digital-twin:sessions:{session_id}"

    def _version(self, data):
        return hashlib.sha256(
            data.encode("utf-8")
        ).hexdigest()

    def create(self):
        session = new_session()
        data = serialize(session)

        created = self.client.set(
            self._key(session.session_id),
            data,
            ex=SESSION_TTL_SECONDS,
            nx=True,
        )

        if not created:
            raise SessionConflictError(
                "Session creation conflict"
            )

        session.version = self._version(data)
        return session

    def load(self, session_id):
        key = self._key(session_id)
        data = self.client.get(key)

        if data is None:
            return None

        session = deserialize(
            data,
            version=self._version(data),
        )

        if expired(session):
            self.delete_if_unchanged_script(
                keys=[key],
                args=[data],
            )
            return None

        return session

    def save(self, session):
        if session.version is None:
            raise ValueError(
                "Session version is required"
            )

        if expired(session):
            raise ValueError(
                "Expired session"
            )

        key = self._key(session.session_id)

        # Read the current value to validate the
        # optimistic concurrency version.
        current_data = self.client.get(key)

        if current_data is None:
            raise ValueError(
                "Unknown or expired session"
            )

        if self._version(current_data) != session.version:
            raise SessionConflictError(
                "Session version conflict"
            )

        # Reject an expired session even if Redis
        # has not removed the key yet.
        current_session = deserialize(current_data)

        if expired(current_session):
            raise ValueError(
                "Unknown or expired session"
            )

        now = utc_now()
        new_expires_at = now + timedelta(
            seconds=SESSION_TTL_SECONDS
        )

        updated_session = ChatSession(
            session_id=session.session_id,
            messages=session.messages,
            updated_at=now,
            expires_at=new_expires_at,
            version=session.version,
        )

        data = serialize(updated_session)

        # Atomic comparison and update.
        # If another request changes the value
        # between GET and this script, saving fails.
        result = self.save_script(
            keys=[key],
            args=[
                current_data,
                data,
                SESSION_TTL_SECONDS,
            ],
        )

        if result == 0:
            raise ValueError(
                "Unknown or expired session"
            )

        if result == -1:
            raise SessionConflictError(
                "Session version conflict"
            )

        if result != 1:
            raise RuntimeError(
                "Unexpected Redis save result"
            )

        # Update the in-memory session only after
        # Redis confirms the write.
        session.updated_at = now
        session.expires_at = new_expires_at
        session.version = self._version(data)

    def delete(self, session_id):
        self.client.delete(
            self._key(session_id)
        )


# --------------------------------------------------
# Storage factory
# --------------------------------------------------

def create_session_store():
    backend = os.getenv(
        "SESSION_STORE_BACKEND",
        "",
    ).strip().lower()

    if backend == "redis":
        return RedisSessionStore(
            redis_url=os.getenv(
                "REDIS_URL",
                "",
            )
        )

    if backend not in ("", "local", "s3"):
        raise ValueError(
            f"Unsupported session storage backend: {backend}"
        )

    use_s3 = (
        backend == "s3"
        or (
            backend == ""
            and os.getenv(
                "USE_S3",
                "false",
            ).lower() == "true"
        )
    )

    if use_s3:
        return S3SessionStore(
            bucket=os.getenv(
                "S3_SESSION_BUCKET",
                "",
            )
        )

    return LocalSessionStore(
        directory=os.getenv(
            "MEMORY_DIR",
            "../memory",
        )
    )
