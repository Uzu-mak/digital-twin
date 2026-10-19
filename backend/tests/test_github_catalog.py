from __future__ import annotations

import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from github_catalog import (
    GitHubClient,
    GitHubRateLimitError,
    fetch_public_repositories,
    public_directory,
    sync_catalog,
)


class FakeGitHubClient:
    def __init__(self) -> None:
        self.json_calls: list[str] = []
        self.text_calls: list[str] = []
        self.repo = {
            "id": 1,
            "name": "sample-project",
            "full_name": "Uzu-mak/sample-project",
            "private": False,
            "description": "A documented sample.",
            "html_url": "https://github.com/Uzu-mak/sample-project",
            "homepage": None,
            "default_branch": "main",
            "language": "Python",
            "languages_url": "https://api.github.com/repos/Uzu-mak/sample-project/languages",
            "stargazers_count": 2,
            "forks_count": 1,
            "open_issues_count": 0,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-02-01T00:00:00Z",
            "pushed_at": "2025-02-01T00:00:00Z",
        }

    def get_json(self, url: str):
        self.json_calls.append(url)
        if "/users/" in url and "/repos?" in url:
            return [self.repo, {**self.repo, "id": 2, "name": "private-project", "full_name": "Uzu-mak/private-project", "private": True}], {}
        if url.endswith("/commits/main"):
            return {"sha": "commit-123"}, {}
        if url.endswith("/languages"):
            return {"Python": 1200, "TypeScript": 300}, {}
        if "/git/trees/commit-123?recursive=1" in url:
            return {
                "truncated": False,
                "tree": [
                    {"path": "README.md", "type": "blob", "sha": "blob-readme", "size": 80},
                    {"path": "docs/architecture.md", "type": "blob", "sha": "blob-architecture", "size": 60},
                    {"path": "src/private_logic.py", "type": "blob", "sha": "blob-code", "size": 50},
                ],
            }, {}
        raise AssertionError(f"Unexpected GitHub API URL: {url}")

    def get_text(self, url: str) -> str:
        self.text_calls.append(url)
        if url.endswith("/README.md"):
            return "# Sample\n\n[Live demo](https://demo.example.test)\n\n[Source](https://github.com/Uzu-mak/sample-project)."
        if url.endswith("/docs/architecture.md"):
            return "# Architecture\n\nComponents are documented here."
        raise AssertionError(f"Unexpected raw GitHub URL: {url}")


class GitHubCatalogTests(unittest.TestCase):
    def test_repository_listing_follows_pagination_and_excludes_private(self):
        class PaginatedClient:
            def __init__(self):
                self.urls = []

            def get_json(self, url):
                self.urls.append(url)
                if len(self.urls) == 1:
                    return [
                        {"full_name": "Uzu-mak/public-one", "private": False},
                        {"full_name": "Uzu-mak/private-one", "private": True},
                    ], {"link": '<https://api.github.com/users/Uzu-mak/repos?page=2>; rel="next"'}
                return [{"full_name": "Uzu-mak/public-two", "private": False}], {}

        client = PaginatedClient()
        repositories = fetch_public_repositories(client, "Uzu-mak")

        self.assertEqual([repo["full_name"] for repo in repositories], ["Uzu-mak/public-one", "Uzu-mak/public-two"])
        self.assertEqual(len(client.urls), 2)

    def test_sync_records_provenance_and_only_selected_markdown(self):
        client = FakeGitHubClient()
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "github_catalog.json"
            catalog = sync_catalog(
                username="Uzu-mak",
                featured_repositories={"sample-project"},
                client=client,
                catalog_path=catalog_path,
            )

            self.assertTrue(catalog_path.is_file())
            persisted = json.loads(catalog_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["repository_count"], 1)
            repository = persisted["repositories"][0]
            self.assertTrue(repository["is_featured"])
            self.assertEqual(repository["languages"], {"Python": 1200, "TypeScript": 300})
            self.assertEqual(repository["last_commit_sha"], "commit-123")
            self.assertEqual([doc["path"] for doc in repository["documents"]], ["README.md", "docs/architecture.md"])
            self.assertEqual(repository["documents"][0]["source_url"], "https://github.com/Uzu-mak/sample-project/blob/commit-123/README.md")
            self.assertEqual(repository["documents"][0]["revision"], "commit-123")
            self.assertEqual(repository["documents"][0]["blob_sha"], "blob-readme")
            self.assertTrue(repository["documents"][0]["last_synchronized_at"])
            self.assertEqual(repository["documented_deployment_links"][0]["url"], "https://demo.example.test")
            self.assertFalse(any("private_logic.py" in url for url in client.text_calls))

    def test_empty_public_repository_is_kept_without_commit_warning(self):
        client = FakeGitHubClient()
        client.repo["size"] = 0
        with tempfile.TemporaryDirectory() as directory:
            catalog = sync_catalog(
                username="Uzu-mak",
                client=client,
                catalog_path=Path(directory) / "github_catalog.json",
            )

        repository = catalog["repositories"][0]
        self.assertEqual(repository["full_name"], "Uzu-mak/sample-project")
        self.assertIsNone(repository["last_commit_sha"])
        self.assertEqual(repository["documents"], [])
        self.assertEqual(catalog["warnings"], [])
        self.assertFalse(any("/commits/" in url or "/git/trees/" in url for url in client.json_calls))

    def test_public_directory_omits_readme_contents(self):
        repository = {
            "id": 1,
            "name": "sample-project",
            "full_name": "Uzu-mak/sample-project",
            "html_url": "https://github.com/Uzu-mak/sample-project",
            "is_featured": True,
            "documents": [{"path": "README.md", "content": "private to backend catalog"}],
        }
        result = public_directory({"username": "Uzu-mak", "repositories": [repository]})
        self.assertEqual(result["repository_count"], 1)
        self.assertNotIn("documents", result["repositories"][0])

    def test_rate_limit_does_not_retry_unboundedly(self):
        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        headers["X-RateLimit-Reset"] = "1234567890"
        error = HTTPError("https://api.github.com/test", 403, "rate limit", headers, None)
        sleep_calls = []
        client = GitHubClient(max_retries=5, sleep=sleep_calls.append)

        with patch("github_catalog.urlopen", side_effect=error):
            with self.assertRaises(GitHubRateLimitError):
                client.get_json("https://api.github.com/test")

        self.assertEqual(sleep_calls, [])


if __name__ == "__main__":
    unittest.main()