from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env so REDIS_URL, GITHUB_TOKEN, etc. apply to the CLI too.
# Values already set in the environment (e.g. GitHub Actions secrets) win.
load_dotenv(Path(__file__).resolve().parent / ".env")

from github_catalog import (  # noqa: E402
    CATALOG_PATH,
    GitHubSyncError,
    featured_repositories_from_env,
    load_catalog,
    load_previous_catalog,
    rollback_catalog,
    sync_catalog,
    uses_redis_catalog,
)


def _destination(catalog_path: Path | None) -> str:
    if uses_redis_catalog(catalog_path):
        return "Redis"

    return str(catalog_path or CATALOG_PATH)


def _describe(label: str, catalog: dict | None) -> None:
    if not catalog:
        print(f"{label}: none")
        return

    documents = sum(
        len(repository.get("documents", []) or [])
        for repository in catalog.get("repositories", [])
    )

    print(
        f"{label}: synchronized {catalog.get('last_synchronized_at') or 'never'}, "
        f"{len(catalog.get('repositories', []))} repositories, "
        f"{documents} documents, "
        f"{len(catalog.get('warnings', []) or [])} warnings"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize public GitHub repositories into the portfolio "
            "catalog (Redis when KNOWLEDGE_STORE_BACKEND=redis, otherwise "
            "a local file)."
        )
    )
    parser.add_argument(
        "--username",
        help="GitHub username; defaults to GITHUB_USERNAME or Uzu-mak.",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        help="Write to this local file instead of the configured backend.",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--status",
        action="store_true",
        help="Show the published and previous catalog snapshots, then exit.",
    )
    mode.add_argument(
        "--rollback",
        action="store_true",
        help="Swap the published catalog with the previous snapshot.",
    )

    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Publish even if GitHub returns no public repositories.",
    )

    arguments = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )

    destination = _destination(arguments.catalog)

    try:
        if arguments.status:
            _describe("Published", load_catalog(arguments.catalog))
            _describe("Previous", load_previous_catalog(arguments.catalog))
            return 0

        if arguments.rollback:
            restored = rollback_catalog(arguments.catalog)
            _describe(f"Rolled back {destination} to", restored)
            return 0

        catalog = sync_catalog(
            username=arguments.username,
            featured_repositories=featured_repositories_from_env(),
            # None lets sync_catalog() publish to Redis when
            # KNOWLEDGE_STORE_BACKEND=redis; --catalog forces a local file.
            catalog_path=arguments.catalog,
            allow_empty=arguments.allow_empty,
        )

    except GitHubSyncError as error:
        action = (
            "status check" if arguments.status
            else "rollback" if arguments.rollback
            else "sync"
        )
        print(f"GitHub {action} failed: {error}", file=sys.stderr)
        print(
            "The published catalog was left unchanged.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Synchronized {catalog['repository_count']} public repositories for "
        f"{catalog['username']} to {destination}."
    )
    _describe("Published", catalog)

    for warning in catalog["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
