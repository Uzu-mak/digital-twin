
import hashlib
import json
import os

from datetime import datetime
from pathlib import Path

from storage import KnowledgeDocument, KnowledgeStore


# --------------------------------------------------
# Shared serialization
# --------------------------------------------------

def validate_document_id(document_id: str) -> str:
    if not isinstance(document_id, str):
        raise ValueError("Invalid document ID")

    if (
        not document_id
        or len(document_id) > 512
        or any(ord(char) < 32 for char in document_id)
    ):
        raise ValueError("Invalid document ID")

    return document_id


def document_key(document_id: str) -> str:
    validate_document_id(document_id)

    digest = hashlib.sha256(
        document_id.encode("utf-8")
    ).hexdigest()

    return f"documents/{digest}.json"


def serialize_document(document: KnowledgeDocument) -> str:
    return json.dumps({
        "document_id": document.document_id,
        "source_url": document.source_url,
        "content": document.content,
        "repository": document.repository,
        "revision": document.revision,
        "updated_at": (
            document.updated_at.isoformat()
            if document.updated_at is not None
            else None
        ),
    })


def deserialize_document(data: str) -> KnowledgeDocument:
    obj = json.loads(data)

    return KnowledgeDocument(
        document_id=validate_document_id(
            obj["document_id"]
        ),
        source_url=obj["source_url"],
        content=obj["content"],
        repository=obj.get("repository"),
        revision=obj.get("revision"),
        updated_at=(
            datetime.fromisoformat(obj["updated_at"])
            if obj.get("updated_at")
            else None
        ),
    )


# --------------------------------------------------
# Local knowledge storage
# --------------------------------------------------

class LocalKnowledgeStore(KnowledgeStore):

    def __init__(self, directory="../knowledge"):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    def _path(self, document_id):
        return self.directory / document_key(document_id).split("/")[-1]

    def put(self, document: KnowledgeDocument) -> None:
        path = self._path(document.document_id)
        path.write_text(
            serialize_document(document),
            encoding="utf-8",
        )

    def get(self, document_id: str) -> KnowledgeDocument | None:
        path = self._path(document_id)

        if not path.is_file() or path.is_symlink():
            return None

        document = deserialize_document(
            path.read_text(encoding="utf-8")
        )

        if document.document_id != document_id:
            raise ValueError("Knowledge document ID mismatch")

        return document

    def list_ids(self) -> list[str]:
        ids = []

        for path in self.directory.glob("*.json"):
            if path.is_symlink():
                continue

            document = deserialize_document(
                path.read_text(encoding="utf-8")
            )

            ids.append(document.document_id)

        return sorted(ids)


# --------------------------------------------------
# AWS S3 knowledge storage
# --------------------------------------------------

class S3KnowledgeStore(KnowledgeStore):

    def __init__(self, bucket, s3_client=None):
        if not bucket:
            raise ValueError("S3 knowledge bucket is required")

        if s3_client is None:
            import boto3
            s3_client = boto3.client("s3")

        self.s3 = s3_client
        self.bucket = bucket

    def put(self, document: KnowledgeDocument) -> None:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=document_key(document.document_id),
            Body=serialize_document(document).encode("utf-8"),
            ContentType="application/json",
        )

    def get(self, document_id: str) -> KnowledgeDocument | None:
        from botocore.exceptions import ClientError

        key = document_key(document_id)

        try:
            response = self.s3.get_object(
                Bucket=self.bucket,
                Key=key,
            )

        except ClientError as exc:
            if exc.response["Error"]["Code"] in (
                "NoSuchKey",
                "404",
                "NotFound",
            ):
                return None

            raise

        document = deserialize_document(
            response["Body"].read().decode("utf-8")
        )

        if document.document_id != document_id:
            raise ValueError("Knowledge document ID mismatch")

        return document

    def list_ids(self) -> list[str]:
        ids = []

        paginator = self.s3.get_paginator(
            "list_objects_v2"
        )

        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix="documents/",
        ):
            for obj in page.get("Contents", []):
                response = self.s3.get_object(
                    Bucket=self.bucket,
                    Key=obj["Key"],
                )

                document = deserialize_document(
                    response["Body"].read().decode("utf-8")
                )

                ids.append(document.document_id)

        return sorted(ids)


# --------------------------------------------------
# Redis knowledge storage (Vercel / Upstash)
# --------------------------------------------------

REDIS_KNOWLEDGE_PREFIX = "digital-twin:knowledge:"
REDIS_KNOWLEDGE_INDEX_KEY = "digital-twin:knowledge:index"


class RedisKnowledgeStore(KnowledgeStore):
    """Persistent document storage for serverless deployments.

    Documents never expire; a sync overwrites them in place. A set of
    document IDs is kept alongside so list_ids() avoids a key scan.
    """

    def __init__(self, redis_url, redis_client=None):
        if redis_client is None:
            if not redis_url:
                raise ValueError("Redis URL is required")

            import redis

            redis_client = redis.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )

        self.client = redis_client

    def _key(self, document_id: str) -> str:
        digest = document_key(document_id).split("/")[-1]
        return f"{REDIS_KNOWLEDGE_PREFIX}{digest}"

    def put(self, document: KnowledgeDocument) -> None:
        key = self._key(document.document_id)

        pipeline = self.client.pipeline()
        pipeline.set(key, serialize_document(document))
        pipeline.sadd(
            REDIS_KNOWLEDGE_INDEX_KEY,
            document.document_id,
        )
        pipeline.execute()

    def get(self, document_id: str) -> KnowledgeDocument | None:
        data = self.client.get(self._key(document_id))

        if data is None:
            return None

        document = deserialize_document(data)

        if document.document_id != document_id:
            raise ValueError("Knowledge document ID mismatch")

        return document

    def list_ids(self) -> list[str]:
        return sorted(
            validate_document_id(document_id)
            for document_id in self.client.smembers(
                REDIS_KNOWLEDGE_INDEX_KEY
            )
        )


# --------------------------------------------------
# Storage factory
# --------------------------------------------------

def create_knowledge_store() -> KnowledgeStore:
    # KNOWLEDGE_STORE_BACKEND selects redis | s3 | local.
    # When unset, the legacy USE_S3 flag still applies.
    backend = os.getenv(
        "KNOWLEDGE_STORE_BACKEND",
        "",
    ).strip().lower()

    if backend == "redis":
        return RedisKnowledgeStore(
            redis_url=os.getenv("REDIS_URL", "").strip()
        )

    if backend not in ("", "local", "s3"):
        raise ValueError(
            f"Unsupported knowledge storage backend: {backend}"
        )

    use_s3 = backend == "s3" or (
        backend == ""
        and os.getenv(
            "USE_S3",
            "false",
        ).lower() == "true"
    )

    if use_s3:
        return S3KnowledgeStore(
            bucket=os.getenv(
                "S3_KNOWLEDGE_BUCKET",
                "",
            )
        )

    return LocalKnowledgeStore(
        directory=os.getenv(
            "KNOWLEDGE_DIR",
            "../knowledge",
        )
    )
