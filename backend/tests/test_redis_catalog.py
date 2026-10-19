
import json

import pytest

import github_catalog


class FakeRedis:
    def __init__(self):
        self.data = {}

    def set(self, key, value):
        self.data[key] = value
        return True

    def get(self, key):
        return self.data.get(key)


@pytest.fixture
def fake_redis(monkeypatch):
    client = FakeRedis()

    monkeypatch.setattr(
        github_catalog,
        "_redis_catalog_client",
        lambda: client,
    )

    return client


def test_write_and_load_catalog(fake_redis):
    catalog = {
        "schema_version": 1,
        "username": "Uzu-mak",
        "last_synchronized_at": "2026-10-19T10:00:00Z",
        "repository_count": 1,
        "repositories": [
            {
                "full_name": "Uzu-mak/AegisOps",
                "documents": [],
            }
        ],
        "warnings": [],
    }

    github_catalog._write_redis_catalog(catalog)

    assert (
        github_catalog.REDIS_CATALOG_KEY
        in fake_redis.data
    )

    loaded = github_catalog._load_redis_catalog()

    assert loaded == catalog


def test_missing_catalog_returns_none(fake_redis):
    assert github_catalog._load_redis_catalog() is None


def test_invalid_catalog_rejected(fake_redis):
    with pytest.raises(github_catalog.GitHubSyncError):
        github_catalog._write_redis_catalog({
            "repositories": "invalid",
        })


def test_corrupt_redis_json_rejected(fake_redis):
    fake_redis.set(
        github_catalog.REDIS_CATALOG_KEY,
        "{invalid json",
    )

    with pytest.raises(github_catalog.GitHubSyncError):
        github_catalog._load_redis_catalog()


def test_catalog_is_stored_without_ttl(fake_redis):
    github_catalog._write_redis_catalog({
        "repositories": [],
    })

    assert (
        github_catalog.REDIS_CATALOG_KEY
        in fake_redis.data
    )
