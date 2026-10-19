from __future__ import annotations

import argparse
import sys
from pathlib import Path

from github_catalog import (
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
            catalog_path=arguments.catalog or CATALOG_PATH,
        )
    except GitHubSyncError as error:
        print(f"GitHub sync failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Synchronized {catalog['repository_count']} public repositories for "
        f"{catalog['username']} to {arguments.catalog or CATALOG_PATH}."
    )
    if catalog["warnings"]:
        print(f"Completed with {len(catalog['warnings'])} repository/document warning(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())