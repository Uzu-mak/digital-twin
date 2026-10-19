from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env so REDIS_URL, GITHUB_TOKEN, etc. apply to the CLI too.
load_dotenv(Path(__file__).resolve().parent / ".env")

from github_catalog import (  # noqa: E402
    CATALOG_PATH,
    GitHubSyncError,
    featured_repositories_from_env,
    sync_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize public GitHub repositories into the local portfolio catalog.")
    parser.add_argument("--username", help="GitHub username; defaults to GITHUB_USERNAME or Uzu-mak.")
    parser.add_argument("--catalog", type=Path, help="Optional catalog output path for local testing.")
    arguments = parser.parse_args()

    try:
        catalog = sync_catalog(
            username=arguments.username,
            featured_repositories=featured_repositories_from_env(),
            # None lets sync_catalog() publish to Redis when
            # KNOWLEDGE_STORE_BACKEND=redis; --catalog forces a local file.
            catalog_path=arguments.catalog,
        )
    except GitHubSyncError as error:
        print(f"GitHub sync failed: {error}", file=sys.stderr)
        return 1

    if arguments.catalog is not None:
        destination = str(arguments.catalog)
    elif os.getenv("KNOWLEDGE_STORE_BACKEND", "local").strip().lower() == "redis":
        destination = "Redis"
    else:
        destination = str(CATALOG_PATH)

    print(
        f"Synchronized {catalog['repository_count']} public repositories for "
        f"{catalog['username']} to {destination}."
    )
    if catalog["warnings"]:
        print(f"Completed with {len(catalog['warnings'])} repository/document warning(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())