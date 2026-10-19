# GitHub sync operations guide

A scheduled GitHub Actions job keeps the portfolio's project directory and the Digital Twin's README knowledge in step with your public repositories on `Uzu-mak`. The deployed API reads everything from Upstash Redis, so a sync never needs a Vercel redeploy.

## How it fits together

```
GitHub API ──► sync_github.py (GitHub Actions, every 6 h) ──► Upstash Redis ──► FastAPI on Vercel
                                                                    │                 ├─ GET /projects
                                                                    │                 └─ POST /chat
```

1. **Discover:** the job lists every repository the account owns, across all pages, and keeps only the public ones.
2. **Store documents:** each README and `docs/*.md` file is stored under a revision-scoped key (`github:<owner>/<repo>:<path>@<commit sha>`). A new sync never overwrites a document the live catalog points to.
3. **Publish:** the new catalog replaces the old one in a single Redis transaction. The old catalog is kept as `digital-twin:github:catalog:previous` for rollback. If anything fails before this step, the live catalog is untouched.
4. **Clean up:** documents that neither the new nor the previous catalog references are deleted. A deleted, renamed or now-private repository disappears from `/projects` and from chat retrieval on the first successful sync after the change. Its stored text is deleted one sync later, once it has left the rollback snapshot too.

Safeguards:

| Situation | What happens |
| --- | --- |
| GitHub rate limit, network failure, bad token, Redis outage | The sync fails with exit code 1; the live catalog is unchanged |
| One repository's GitHub calls fail | That repository keeps its documentation from the last successful sync, and a warning is logged |
| GitHub returns zero repositories while the live catalog has some | The sync refuses to publish unless run with `--allow-empty` |
| Two syncs at once | A Redis lock (`digital-twin:github:sync-lock`, 30 min expiry) rejects the second run; the workflow's concurrency group also queues runs |
| Corrupt catalog in Redis | `/projects` returns 503; the next sync replaces it |
| Redis unreachable | `/projects` returns 503; `/chat` still answers from the profile, without repository evidence, and logs the error |

`/projects` sends `Cache-Control: public, max-age=60, s-maxage=300, stale-while-revalidate=600`, so new data shows up within about five minutes of a sync.

## Configuration

### Already in the repository

- The workflow `.github/workflows/sync-github.yml` runs at minute 0 every 6 hours (UTC) and on manual dispatch.
- It authenticates to GitHub with the workflow's built-in token (read-only; 1,000 API requests/hour).
- Defaults: username `Uzu-mak`; featured repositories AegisOps, Consultinp-app and CareerOps.

### You still need to add

**GitHub repository secret** (Settings → Secrets and variables → Actions → New repository secret):

| Name | Value |
| --- | --- |
| `REDIS_URL` | Your Upstash Redis URL, the same one the Vercel backend uses (`rediss://default:<password>@<host>:6379`) |

**Optional GitHub repository variables** (same page, Variables tab):

| Name | Purpose |
| --- | --- |
| `PORTFOLIO_GITHUB_USERNAME` | Sync a different account (default `Uzu-mak`) |
| `PORTFOLIO_FEATURED_REPOSITORIES` | Comma-separated names to feature instead of the defaults |

**Vercel backend environment variables** (check they are set):

| Name | Value |
| --- | --- |
| `KNOWLEDGE_STORE_BACKEND` | `redis` |
| `SESSION_STORE_BACKEND` | `redis` |
| `REDIS_URL` | Your Upstash Redis URL |
| `CORS_ORIGINS` | `https://digital-twin-theta-gules.vercel.app` |
| `OPENAI_API_KEY` | Your OpenAI key |

The Vercel backend does not need `GITHUB_TOKEN`; only the sync job talks to GitHub.

## Running a sync

**Manually in GitHub:** Actions → *Sync GitHub catalog* → *Run workflow*. Tick *allow_empty* only if you really have no public repositories.

**Locally against production:** put the production `REDIS_URL` and `KNOWLEDGE_STORE_BACKEND=redis` in `backend/.env`, then from the repository root run:

```
uv run --project backend --directory backend python sync_github.py
```

**Locally without Redis (development):** leave `KNOWLEDGE_STORE_BACKEND` unset. The catalog is written to `backend/data/github_catalog.json`, and documents go to `knowledge/`.

## Verifying

- **Last successful sync:** `python sync_github.py --status` prints the published and previous snapshots with their timestamps, repository and document counts. The workflow prints the same before and after each run. The live site shows the same timestamp in the project directory's "Synced …" label.
- **`/projects`:** `https://digital-twin-api-one.vercel.app/projects` should list every public repository, with `last_synchronized_at` matching the latest run.
- **Grounded `/chat`:** ask the twin about a repository whose README you just changed. The answer should cite `github.com/Uzu-mak/<repo>/blob/<new commit sha>/README.md`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Workflow fails with "REDIS_URL is not set" | Add the repository secret above |
| "GitHub API rate limit reached" | Wait for the reset time in the message, or rerun later. The built-in token allows 1,000 requests/hour; a sync uses about 3 API requests per repository (README downloads come from raw.githubusercontent.com and don't count) |
| "Another GitHub synchronization is already running" | A run is in progress, or a crashed run's lock has not expired yet (30 min) |
| "Redis catalog storage is unavailable" | Check the Upstash dashboard and that `REDIS_URL` is current |
| "refusing to publish an empty catalog" | GitHub returned no public repositories. If that is correct, rerun with *allow_empty* |
| `/projects` returns 503 | Redis is unreachable or the catalog is corrupt; run `--status`, then a sync |
| New repo missing on the site | Confirm the sync succeeded, then allow up to 5 minutes of CDN caching |

## Rolling back a bad sync

```
uv run --project backend --directory backend python sync_github.py --rollback
```

This swaps the published catalog with the previous snapshot. Its documents are still stored, so chat works immediately. Running it again swaps back. The next scheduled sync publishes fresh data, so if the bad data came from GitHub itself, fix the repository first or disable the workflow (Actions → *Sync GitHub catalog* → ⋯ → *Disable workflow*).

## Rotating credentials

- **Upstash Redis password:** reset it in Upstash, then update `REDIS_URL` in the Vercel backend project (and redeploy so it takes effect), in the `REDIS_URL` GitHub secret, and in your local `backend/.env`.
- **GitHub token:** the workflow token is issued per run and needs no rotation. If you set a personal `GITHUB_TOKEN` in `backend/.env` for local runs, use a fine-grained, read-only token and regenerate it on GitHub when it expires.

## Tests

```
uv run --project backend --directory backend --with pytest --with boto3 python -m pytest tests -v
```

Several test files use pytest, and the S3 tests need `boto3`; neither is a runtime dependency, so `--with` adds them for the test run only. GitHub and Redis are mocked; no credentials are needed. To exercise a real Redis, set `DIGITAL_TWIN_TEST_REDIS_URL` to a separate, empty test database before running `tests/test_redis_live.py`. It writes and deletes the production key names, and it refuses to run against your `REDIS_URL`.
