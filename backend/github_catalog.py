
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

if __package__:
    from .knowledge_store import create_knowledge_store
    from .storage import KnowledgeDocument, KnowledgeStore
else:
    from knowledge_store import create_knowledge_store
    from storage import KnowledgeDocument, KnowledgeStore


BACKEND_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BACKEND_DIR / "data" / "github_catalog.json"
API_ROOT = "https://api.github.com"
MAX_DOCUMENT_BYTES = 150_000
MAX_DOCUMENTS_PER_REPOSITORY = 6

logger = logging.getLogger(__name__)

DEFAULT_FEATURED_REPOSITORIES = (
    "AegisOps-AI-Multimodal-Enterprise-GraphRAG-Reliability-Platform",
    "Consultinp-app",
    "CareerOps",
)


class GitHubSyncError(RuntimeError):
    """A safe, user-facing GitHub synchronization error."""


class GitHubRateLimitError(GitHubSyncError):
    """GitHub's API quota prevented a complete synchronization."""


class GitHubNotFoundError(GitHubSyncError):
    """An optional repository file or endpoint was not found."""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def safe_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    parsed = urlparse(value.strip())

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    return value.strip()


def featured_repositories_from_env() -> set[str]:
    configured = os.getenv("GITHUB_FEATURED_REPOSITORIES", "")

    values = (
        configured.split(",")
        if configured.strip()
        else DEFAULT_FEATURED_REPOSITORIES
    )

    return {
        value.strip().lower()
        for value in values
        if value.strip()
    }


def _next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None

    for part in link_header.split(","):
        match = re.match(
            r'\s*<([^>]+)>\s*;\s*rel="([^"]+)"',
            part,
        )

        if match and match.group(2) == "next":
            return match.group(1)

    return None


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        timeout: float = 10,
        max_retries: int = 2,
        sleep=time.sleep,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep

        self.headers = {
            "User-Agent": "TwinPortfolio-GitHub-Sync",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _request(
        self,
        url: str,
        *,
        as_json: bool,
        max_bytes: int = MAX_DOCUMENT_BYTES,
    ):
        parsed = urlparse(url)

        if (
            parsed.scheme != "https"
            or parsed.hostname not in {
                "api.github.com",
                "raw.githubusercontent.com",
            }
        ):
            raise GitHubSyncError(
                "Refusing to request a non-GitHub URL."
            )

        headers = dict(self.headers)

        if parsed.hostname == "raw.githubusercontent.com":
            headers["Accept"] = "text/plain"

        request = Request(url, headers=headers)

        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(
                    request,
                    timeout=self.timeout,
                ) as response:
                    body = response.read(max_bytes + 1)

                    if len(body) > max_bytes:
                        raise GitHubSyncError(
                            "A selected GitHub document exceeds "
                            "the configured size limit."
                        )

                    response_headers = {
                        key.lower(): value
                        for key, value in response.headers.items()
                    }

                    if as_json:
                        try:
                            return (
                                json.loads(body.decode("utf-8")),
                                response_headers,
                            )
                        except (
                            UnicodeDecodeError,
                            json.JSONDecodeError,
                        ) as error:
                            raise GitHubSyncError(
                                "GitHub returned an invalid JSON response."
                            ) from error

                    return (
                        body.decode("utf-8", errors="replace"),
                        response_headers,
                    )

            except HTTPError as error:
                response_headers = {
                    key.lower(): value
                    for key, value in error.headers.items()
                }

                remaining = response_headers.get(
                    "x-ratelimit-remaining"
                )

                if error.code == 404:
                    raise GitHubNotFoundError(
                        "A selected GitHub resource was not found."
                    ) from error

                if error.code == 403 and remaining == "0":
                    reset = response_headers.get(
                        "x-ratelimit-reset"
                    )

                    detail = (
                        f"GitHub API rate limit reached "
                        f"(reset epoch {reset})."
                        if reset
                        else "GitHub API rate limit reached."
                    )

                    raise GitHubRateLimitError(
                        detail
                    ) from error

                if error.code == 429:
                    retry_after = response_headers.get(
                        "retry-after",
                        "",
                    )

                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = 0

                    if (
                        attempt < self.max_retries
                        and 0 < delay <= 3
                    ):
                        self.sleep(delay)
                        continue

                    raise GitHubRateLimitError(
                        "GitHub asked the synchronizer to slow down; "
                        "no catalog was written."
                    ) from error

                if (
                    error.code in {500, 502, 503, 504}
                    and attempt < self.max_retries
                ):
                    self.sleep(0.25 * (2 ** attempt))
                    continue

                raise GitHubSyncError(
                    f"GitHub request failed with HTTP {error.code}."
                ) from error

            except (TimeoutError, URLError) as error:
                if attempt < self.max_retries:
                    self.sleep(0.25 * (2 ** attempt))
                    continue

                raise GitHubSyncError(
                    "GitHub request timed out or could not connect."
                ) from error

        raise GitHubSyncError(
            "GitHub request failed after bounded retries."
        )

    def get_json(
        self,
        url: str,
    ) -> tuple[Any, dict[str, str]]:
        return self._request(
            url,
            as_json=True,
            max_bytes=10_000_000,
        )

    def get_text(self, url: str) -> str:
        content, _ = self._request(
            url,
            as_json=False,
        )
        return content


def fetch_public_repositories(
    client: GitHubClient,
    username: str,
) -> list[dict[str, Any]]:
    if not re.fullmatch(
        r"[A-Za-z0-9-]{1,39}",
        username,
    ):
        raise GitHubSyncError(
            "GITHUB_USERNAME must be a valid GitHub username."
        )

    url = (
        f"{API_ROOT}/users/{quote(username, safe='')}/repos"
        "?type=owner&per_page=100&sort=updated"
    )

    repositories: list[dict[str, Any]] = []

    while url:
        payload, headers = client.get_json(url)

        if not isinstance(payload, list):
            raise GitHubSyncError(
                "GitHub returned an invalid repository list."
            )

        repositories.extend(
            repo
            for repo in payload
            if isinstance(repo, dict)
            and repo.get("private") is False
            and isinstance(repo.get("full_name"), str)
        )

        next_url = _next_link(headers.get("link"))

        if next_url:
            next_parsed = urlparse(next_url)

            if (
                next_parsed.scheme != "https"
                or next_parsed.hostname != "api.github.com"
            ):
                raise GitHubSyncError(
                    "GitHub returned an unsafe pagination URL."
                )

        url = next_url

    return repositories


def _is_selected_markdown(path: str) -> bool:
    normalized = path.lower()
    parts = normalized.split("/")

    if not normalized.endswith(".md"):
        return False

    if len(parts) == 1:
        return (
            parts[0].startswith("readme")
            or parts[0] in {
                "architecture.md",
                "overview.md",
                "contributing.md",
            }
        )

    return parts[0] == "docs" and len(parts) > 1


def _document_priority(
    path: str,
) -> tuple[int, str]:
    normalized = path.lower()

    if (
        "/" not in normalized
        and normalized.startswith("readme")
    ):
        return 0, normalized

    if "/" not in normalized:
        return 1, normalized

    return 2, normalized


def _documented_links(
    markdown: str,
    source_path: str,
    source_url: str,
    revision: str,
) -> list[dict[str, str]]:
    pattern = re.compile(
        r"\[([^\]]{1,120})\]\((https?://[^)\s]+)\)"
    )

    link_labels = re.compile(
        r"\b(demo|live|deployment|deployed|website|site|application|product)\b",
        re.I,
    )

    found: list[dict[str, str]] = []
    seen: set[str] = set()

    for label, candidate in pattern.findall(markdown):
        url = safe_http_url(
            candidate.rstrip(".,;")
        )

        if (
            not url
            or not link_labels.search(label)
            or url in seen
        ):
            continue

        seen.add(url)

        found.append({
            "label": label.strip(),
            "url": url,
            "source_path": source_path,
            "source_url": source_url,
            "revision": revision,
        })

    return found


def _normalized_repository(
    repository: dict[str, Any],
    languages: dict[str, Any],
    revision: str | None,
    documents: list[dict[str, Any]],
    featured: set[str],
) -> dict[str, Any]:
    full_name = repository["full_name"]

    name = str(
        repository.get(
            "name",
            full_name.split("/", 1)[-1],
        )
    )

    return {
        "id": repository.get("id"),
        "name": name,
        "full_name": full_name,
        "description": repository.get("description"),
        "html_url": safe_http_url(
            repository.get("html_url")
        ),
        "homepage": safe_http_url(
            repository.get("homepage")
        ),
        "default_branch": repository.get("default_branch"),
        "language": repository.get("language"),
        "languages": languages,
        "stargazers_count": repository.get(
            "stargazers_count",
            0,
        ),
        "forks_count": repository.get(
            "forks_count",
            0,
        ),
        "open_issues_count": repository.get(
            "open_issues_count",
            0,
        ),
        "created_at": repository.get("created_at"),
        "updated_at": repository.get("updated_at"),
        "pushed_at": repository.get("pushed_at"),
        "last_commit_sha": revision,
        "is_featured": (
            full_name.lower() in featured
            or name.lower() in featured
        ),
        "documents": documents,
        "documented_deployment_links": [
            link
            for document in documents
            for link in document.get(
                "documented_deployment_links",
                [],
            )
        ],
    }


def _sync_repository(
    client: GitHubClient,
    repository: dict[str, Any],
    featured: set[str],
    synced_at: str,
) -> tuple[dict[str, Any], list[str]]:
    full_name = repository["full_name"]
    default_branch = repository.get("default_branch")

    warnings: list[str] = []
    revision: str | None = None
    languages: dict[str, Any] = {}
    documents: list[dict[str, Any]] = []

    repository_size = repository.get("size")

    is_empty_repository = (
        isinstance(repository_size, int)
        and repository_size == 0
    )

    if default_branch and not is_empty_repository:
        try:
            commit, _ = client.get_json(
                f"{API_ROOT}/repos/{full_name}/commits/"
                f"{quote(str(default_branch), safe='')}"
            )

            revision = (
                commit.get("sha")
                if isinstance(commit, dict)
                else None
            )

            if not isinstance(revision, str):
                raise GitHubSyncError(
                    "GitHub did not return a commit SHA."
                )

        except GitHubRateLimitError:
            raise

        except GitHubSyncError as error:
            warnings.append(
                f"{full_name}: commit lookup failed: {error}"
            )

    try:
        languages_url = repository.get(
            "languages_url"
        )

        if languages_url:
            languages_payload, _ = client.get_json(
                languages_url
            )

            if isinstance(languages_payload, dict):
                languages = languages_payload

    except GitHubRateLimitError:
        raise

    except GitHubSyncError as error:
        warnings.append(
            f"{full_name}: language lookup failed: {error}"
        )

    if revision:
        try:
            tree_payload, _ = client.get_json(
                f"{API_ROOT}/repos/{full_name}/git/trees/"
                f"{revision}?recursive=1"
            )

            tree = (
                tree_payload.get("tree", [])
                if isinstance(tree_payload, dict)
                else []
            )

            entries = [
                item
                for item in tree
                if isinstance(item, dict)
                and item.get("type") == "blob"
                and isinstance(item.get("path"), str)
                and _is_selected_markdown(item["path"])
                and isinstance(item.get("sha"), str)
                and int(item.get("size", 0))
                <= MAX_DOCUMENT_BYTES
            ]

            entries.sort(
                key=lambda item: _document_priority(
                    item["path"]
                )
            )

            for item in entries[
                :MAX_DOCUMENTS_PER_REPOSITORY
            ]:
                path = item["path"]

                encoded_path = "/".join(
                    quote(part, safe="")
                    for part in path.split("/")
                )

                raw_url = (
                    "https://raw.githubusercontent.com/"
                    f"{full_name}/{revision}/{encoded_path}"
                )

                source_url = (
                    "https://github.com/"
                    f"{full_name}/blob/{revision}/{encoded_path}"
                )

                try:
                    content = client.get_text(
                        raw_url
                    )

                except GitHubNotFoundError:
                    continue

                except GitHubRateLimitError:
                    raise

                except GitHubSyncError as error:
                    warnings.append(
                        f"{full_name}/{path}: "
                        f"document download failed: {error}"
                    )
                    continue

                documents.append({
                    "path": path,
                    "source_url": source_url,
                    "revision": revision,
                    "blob_sha": item["sha"],
                    "last_synchronized_at": synced_at,
                    "content": content,
                    "documented_deployment_links": (
                        _documented_links(
                            content,
                            path,
                            source_url,
                            revision,
                        )
                    ),
                })

            if (
                isinstance(tree_payload, dict)
                and tree_payload.get("truncated")
            ):
                warnings.append(
                    f"{full_name}: GitHub truncated the "
                    "recursive file tree; selected docs "
                    "may be incomplete."
                )

        except GitHubRateLimitError:
            raise

        except GitHubSyncError as error:
            warnings.append(
                f"{full_name}: documentation lookup failed: {error}"
            )

    return (
        _normalized_repository(
            repository,
            languages,
            revision,
            documents,
            featured,
        ),
        warnings,
    )



# --------------------------------------------------
# Catalog storage (Redis in production, local file in development)
# --------------------------------------------------

REDIS_CATALOG_KEY = "digital-twin:github:catalog"
REDIS_PREVIOUS_CATALOG_KEY = "digital-twin:github:catalog:previous"
REDIS_SYNC_LOCK_KEY = "digital-twin:github:sync-lock"

# A sync normally takes a minute or two; the lock expires on its own if a
# runner dies mid-sync, so a crashed job cannot block syncs forever.
SYNC_LOCK_TTL_SECONDS = 30 * 60

# Compare-and-delete so a slow run never releases a lock that has since
# expired and been taken by another run.
_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class GitHubStorageError(GitHubSyncError):
    """Catalog storage (Redis or the local file) could not be reached."""


class GitHubSyncInProgressError(GitHubSyncError):
    """Another synchronization run currently holds the sync lock."""


def _validate_catalog(catalog: Any) -> dict[str, Any]:
    if (
        not isinstance(catalog, dict)
        or not isinstance(catalog.get("repositories"), list)
    ):
        raise GitHubSyncError(
            "The GitHub catalog has an invalid format."
        )

    for repository in catalog["repositories"]:
        if (
            not isinstance(repository, dict)
            or not isinstance(repository.get("full_name"), str)
            or not isinstance(repository.get("documents", []), list)
        ):
            raise GitHubSyncError(
                "The GitHub catalog has an invalid repository entry."
            )

    return catalog


def uses_redis_catalog(catalog_path: Path | None = None) -> bool:
    """Redis mode applies only when no explicit local path is given."""
    return (
        catalog_path is None
        and os.getenv("KNOWLEDGE_STORE_BACKEND", "local").strip().lower()
        == "redis"
    )


def _redis_catalog_client():
    redis_url = os.getenv("REDIS_URL", "").strip()

    if not redis_url:
        raise GitHubSyncError(
            "Redis catalog storage is not configured."
        )

    import redis

    return redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def _redis_errors() -> tuple[type[BaseException], ...]:
    try:
        import redis
    except ImportError:
        return (ConnectionError, TimeoutError)

    return (redis.exceptions.RedisError, ConnectionError, TimeoutError)


def _parse_catalog(raw: Any, label: str) -> dict[str, Any]:
    try:
        catalog = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise GitHubSyncError(
            f"The {label} GitHub catalog is invalid."
        ) from error

    return _validate_catalog(catalog)


def _write_redis_catalog(catalog: dict[str, Any]) -> None:
    """Publish a catalog and keep the one it replaces for rollback.

    The new catalog and the previous-snapshot copy are written in one
    MULTI/EXEC transaction, so readers see either the old catalog or the
    new one, never a mix.
    """
    validated = _validate_catalog(catalog)
    client = _redis_catalog_client()

    try:
        current = client.get(REDIS_CATALOG_KEY)

        pipeline = client.pipeline(transaction=True)

        if current is not None:
            pipeline.set(REDIS_PREVIOUS_CATALOG_KEY, current)

        pipeline.set(
            REDIS_CATALOG_KEY,
            json.dumps(validated, ensure_ascii=False),
        )
        pipeline.execute()

    except _redis_errors() as error:
        raise GitHubStorageError(
            "Redis catalog storage is unavailable; "
            "the previous catalog was left in place."
        ) from error


def _load_redis_catalog(
    key: str = REDIS_CATALOG_KEY,
) -> dict[str, Any] | None:
    client = _redis_catalog_client()

    try:
        raw = client.get(key)
    except _redis_errors() as error:
        raise GitHubStorageError(
            "Redis catalog storage is unavailable."
        ) from error

    if raw is None:
        return None

    return _parse_catalog(raw, "Redis")


def _rollback_redis_catalog() -> dict[str, Any]:
    client = _redis_catalog_client()

    try:
        current = client.get(REDIS_CATALOG_KEY)
        previous = client.get(REDIS_PREVIOUS_CATALOG_KEY)

        if previous is None:
            raise GitHubSyncError(
                "No previous catalog snapshot is available."
            )

        restored = _parse_catalog(previous, "previous Redis")

        pipeline = client.pipeline(transaction=True)
        pipeline.set(REDIS_CATALOG_KEY, previous)

        if current is not None:
            pipeline.set(REDIS_PREVIOUS_CATALOG_KEY, current)

        pipeline.execute()

    except _redis_errors() as error:
        raise GitHubStorageError(
            "Redis catalog storage is unavailable."
        ) from error

    return restored


class _RedisSyncLock:
    """Prevents two sync runs from publishing or cleaning up at once."""

    def __init__(self, client) -> None:
        self.client = client
        self.token = os.urandom(16).hex()

    def __enter__(self):
        try:
            acquired = self.client.set(
                REDIS_SYNC_LOCK_KEY,
                self.token,
                nx=True,
                ex=SYNC_LOCK_TTL_SECONDS,
            )
        except _redis_errors() as error:
            raise GitHubStorageError(
                "Redis is unavailable; synchronization did not start."
            ) from error

        if not acquired:
            raise GitHubSyncInProgressError(
                "Another GitHub synchronization is already running."
            )

        return self

    def __exit__(self, *exc_info) -> None:
        try:
            release = self.client.register_script(_RELEASE_LOCK_SCRIPT)
            release(keys=[REDIS_SYNC_LOCK_KEY], args=[self.token])
        except _redis_errors():
            # The lock expires on its own; never mask the sync result.
            logger.warning("Could not release the GitHub sync lock.")


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        return None


def _previous_catalog_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.previous{path.suffix}")


def _write_catalog(
    path: Path,
    catalog: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(
                temporary_file.name
            )

            json.dump(
                catalog,
                temporary_file,
                indent=2,
                ensure_ascii=False,
            )

            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(
                temporary_file.fileno()
            )

        os.replace(
            temporary_path,
            path,
        )

    finally:
        if (
            temporary_path
            and temporary_path.exists()
        ):
            temporary_path.unlink()


def _read_current_catalog(
    use_redis: bool,
    catalog_path: Path,
) -> dict[str, Any] | None:
    """Read the published catalog for carry-over and cleanup decisions.

    A corrupt catalog is treated as absent so a sync can repair it, but a
    storage outage still fails the sync before anything is written.
    """
    try:
        if use_redis:
            return _load_redis_catalog()

        if not catalog_path.is_file():
            return None

        return _parse_catalog(
            catalog_path.read_text(encoding="utf-8"),
            "local",
        )

    except GitHubStorageError:
        raise

    except (GitHubSyncError, OSError):
        logger.warning(
            "The published GitHub catalog is unreadable; "
            "it will be replaced by this sync."
        )
        return None


def _storage_ids(catalog: dict[str, Any] | None) -> set[str]:
    """Every knowledge-store ID a catalog references."""
    if not catalog:
        return set()

    referenced: set[str] = set()

    for repository in catalog.get("repositories", []):
        if not isinstance(repository, dict):
            continue

        for document in repository.get("documents", []) or []:
            if not isinstance(document, dict):
                continue

            # Legacy catalogs stored documents under the plain document ID.
            value = document.get("storage_id", document.get("document_id"))

            if isinstance(value, str):
                referenced.add(value)

    return referenced


def _documentation_failed(
    full_name: str,
    warnings: list[str],
) -> bool:
    prefixes = (
        f"{full_name}: commit lookup failed",
        f"{full_name}: documentation lookup failed",
    )

    return any(warning.startswith(prefixes) for warning in warnings)


def _cleanup_stale_documents(
    store: KnowledgeStore,
    keep: set[str],
) -> int:
    """Delete GitHub documents that no published snapshot references.

    Runs only after a successful publish. Documents referenced by either
    the new catalog or the snapshot it replaced are kept, so readers that
    loaded the old catalog a moment ago and a later rollback still work.
    """
    try:
        stored_ids = store.list_ids()
    except NotImplementedError:
        return 0

    removed = 0

    for document_id in stored_ids:
        if not document_id.startswith("github:") or document_id in keep:
            continue

        try:
            store.delete(document_id)
            removed += 1
        except NotImplementedError:
            return removed

    return removed


def sync_catalog(
    username: str | None = None,
    token: str | None = None,
    featured_repositories: set[str] | None = None,
    client: GitHubClient | None = None,
    catalog_path: Path | None = None,
    knowledge_store: KnowledgeStore | None = None,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Synchronize public repositories and publish a new catalog snapshot.

    Order of operations:
    1. Take the sync lock (Redis mode) so runs cannot overlap.
    2. Store each README/doc under a revision-scoped ID
       (``<document_id>@<commit sha>``). Documents of the published
       catalog are never overwritten, so readers keep a consistent view.
    3. Publish the new catalog in one write, keeping the old one as the
       rollback snapshot. Any failure before this step leaves the
       published catalog untouched.
    4. Delete documents that neither snapshot references.
    """
    username = (
        username
        or os.getenv(
            "GITHUB_USERNAME",
            "Uzu-mak",
        )
    ).strip()

    token = (
        token
        if token is not None
        else os.getenv("GITHUB_TOKEN")
    )

    featured = (
        featured_repositories
        if featured_repositories is not None
        else featured_repositories_from_env()
    )

    client = (
        client
        if client is not None
        else GitHubClient(token=token)
    )

    use_redis = uses_redis_catalog(catalog_path)

    catalog_path = (
        catalog_path
        if catalog_path is not None
        else CATALOG_PATH
    )

    lock = (
        _RedisSyncLock(_redis_catalog_client())
        if use_redis
        else _NoLock()
    )

    with lock:
        current_catalog = _read_current_catalog(use_redis, catalog_path)

        current_by_name = {
            repository["full_name"].lower(): repository
            for repository in (current_catalog or {}).get(
                "repositories", []
            )
            if isinstance(repository, dict)
            and isinstance(repository.get("full_name"), str)
        }

        synced_at = utc_now()

        public_repositories = fetch_public_repositories(
            client,
            username,
        )

        store = (
            knowledge_store
            if knowledge_store is not None
            else create_knowledge_store()
        )

        repositories: list[dict[str, Any]] = []
        warnings: list[str] = []

        for repository in public_repositories:
            normalized, repo_warnings = _sync_repository(
                client,
                repository,
                featured,
                synced_at,
            )

            full_name = normalized["full_name"]

            for document in normalized["documents"]:
                document_id = (
                    f"github:{full_name}:{document['path']}"
                )
                storage_id = f"{document_id}@{document['revision']}"

                knowledge_document = KnowledgeDocument(
                    document_id=storage_id,
                    source_url=document["source_url"],
                    content=document["content"],
                    repository=full_name,
                    revision=document.get("revision"),
                    updated_at=datetime.now(timezone.utc),
                )

                # Store content before publishing its reference.
                store.put(knowledge_document)

                document["document_id"] = document_id
                document["storage_id"] = storage_id

                # Keep metadata in the catalog, not full text.
                del document["content"]

            previous = current_by_name.get(full_name.lower())

            # A transient GitHub failure for one repository should not
            # erase its last known good documentation.
            if (
                not normalized["documents"]
                and previous
                and previous.get("documents")
                and _documentation_failed(full_name, repo_warnings)
            ):
                normalized["documents"] = previous["documents"]
                normalized["documented_deployment_links"] = previous.get(
                    "documented_deployment_links",
                    [],
                )
                repo_warnings.append(
                    f"{full_name}: kept documentation from the previous "
                    "successful sync."
                )

            repositories.append(normalized)
            warnings.extend(repo_warnings)

        if (
            not repositories
            and current_catalog
            and current_catalog.get("repositories")
            and not allow_empty
        ):
            raise GitHubSyncError(
                "GitHub returned no public repositories but the published "
                "catalog has some; refusing to publish an empty catalog. "
                "Re-run with allow_empty=True (--allow-empty) if this is "
                "intended."
            )

        catalog = _validate_catalog({
            "schema_version": 1,
            "username": username,
            "last_synchronized_at": synced_at,
            "repository_count": len(repositories),
            "repositories": repositories,
            "warnings": warnings,
        })

        # Publish only after all document writes have succeeded.
        if use_redis:
            _write_redis_catalog(catalog)
        else:
            previous_path = _previous_catalog_path(catalog_path)

            if current_catalog is not None:
                _write_catalog(previous_path, current_catalog)

            _write_catalog(catalog_path, catalog)

        try:
            removed = _cleanup_stale_documents(
                store,
                _storage_ids(catalog) | _storage_ids(current_catalog),
            )
            logger.info("Removed %d stale knowledge document(s).", removed)

        except Exception:
            # Publication already succeeded; stale documents are not
            # referenced by the catalog, so retrieval cannot use them.
            logger.exception("Stale knowledge-document cleanup failed.")

    return catalog


def _empty_catalog() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "username": os.getenv("GITHUB_USERNAME", "Uzu-mak"),
        "last_synchronized_at": None,
        "repository_count": 0,
        "repositories": [],
        "warnings": [],
    }


def load_catalog(
    catalog_path: Path | None = None,
) -> dict[str, Any]:
    """Load the published catalog.

    Redis mode raises GitHubStorageError when Redis is unreachable rather
    than pretending the catalog is empty.
    """
    # Production Redis mode; explicit paths remain local for tests.
    if uses_redis_catalog(catalog_path):
        catalog = _load_redis_catalog()
        return catalog if catalog is not None else _empty_catalog()

    path = catalog_path if catalog_path is not None else CATALOG_PATH

    if not path.is_file():
        return _empty_catalog()

    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GitHubSyncError(
            "The local GitHub catalog is unavailable or invalid."
        ) from error

    return _validate_catalog(catalog)


def load_previous_catalog(
    catalog_path: Path | None = None,
) -> dict[str, Any] | None:
    """Load the rollback snapshot, if one exists."""
    if uses_redis_catalog(catalog_path):
        return _load_redis_catalog(REDIS_PREVIOUS_CATALOG_KEY)

    path = _previous_catalog_path(
        catalog_path if catalog_path is not None else CATALOG_PATH
    )

    if not path.is_file():
        return None

    return _parse_catalog(path.read_text(encoding="utf-8"), "local")


def rollback_catalog(
    catalog_path: Path | None = None,
) -> dict[str, Any]:
    """Swap the published catalog with its previous snapshot."""
    if uses_redis_catalog(catalog_path):
        return _rollback_redis_catalog()

    path = catalog_path if catalog_path is not None else CATALOG_PATH
    previous_path = _previous_catalog_path(path)

    if not previous_path.is_file():
        raise GitHubSyncError(
            "No previous catalog snapshot is available."
        )

    restored = _parse_catalog(
        previous_path.read_text(encoding="utf-8"),
        "local",
    )

    current = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.is_file()
        else None
    )

    _write_catalog(path, restored)

    if current is not None:
        _write_catalog(previous_path, current)

    return restored


def public_directory(
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = (
        catalog
        if catalog is not None
        else load_catalog()
    )

    fields = (
        "id",
        "name",
        "full_name",
        "description",
        "html_url",
        "homepage",
        "default_branch",
        "language",
        "languages",
        "stargazers_count",
        "forks_count",
        "open_issues_count",
        "created_at",
        "updated_at",
        "pushed_at",
        "last_commit_sha",
        "is_featured",
        "documented_deployment_links",
    )

    repositories = [
        {
            key: repository.get(key)
            for key in fields
        }
        for repository in catalog.get(
            "repositories",
            [],
        )
        if isinstance(repository, dict)
        and repository.get("html_url")
    ]

    repositories.sort(
        key=lambda repository: (
            not repository.get(
                "is_featured",
                False,
            ),
            str(
                repository.get(
                    "updated_at"
                ) or ""
            ),
        ),
        reverse=False,
    )

    return {
        "username": catalog.get("username"),
        "last_synchronized_at": catalog.get(
            "last_synchronized_at"
        ),
        "repository_count": len(repositories),
        "repositories": repositories,
    }
