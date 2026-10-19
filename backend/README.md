# Backend

FastAPI serves the Digital Twin API and a read-only public project directory. GitHub synchronization is a separate command and never runs as part of a visitor chat request.

## Environment

Copy `.env.example` to `.env` and set `OPENAI_API_KEY` for chat. `GITHUB_USERNAME` selects the public account to synchronize. `GITHUB_TOKEN` is optional and should be a fine-grained, read-only token; it increases the GitHub API rate limit. `GITHUB_FEATURED_REPOSITORIES` is an optional comma-separated list of repository names or `owner/name` slugs. `CORS_ORIGINS` is a comma-separated allowlist. Do not commit `.env`.

The frontend optionally reads `NEXT_PUBLIC_API_URL`; its default is `http://localhost:8000`.

## Local startup

From the repository root, start the API:

```powershell
uv run --project backend --directory backend uvicorn server:app --reload --port 8000
```

Start the frontend in another terminal:

```powershell
npm.cmd --prefix frontend run dev
```

The API root and `/health` are startup checks. `/chat` retains the existing request (`message`, optional `session_id`) and response (`response`, `session_id`) contract. `/projects` returns public repository metadata and documented links; README bodies remain in the local backend catalog and are not sent to the browser.

## GitHub synchronization

Run synchronization manually, independently from chat:

```powershell
uv run --project backend --directory backend python sync_github.py
```

The command paginates the configured user's repository list, keeps only repositories explicitly marked public, and reads metadata, languages, the latest commit SHA, README files, and a bounded selection of root/docs Markdown. It does not clone repositories or read arbitrary source files. HTTP(S) links labeled as demos or deployments are retained with their Markdown source URL, file path, and commit revision. Missing optional documentation produces a warning; rate limits and top-level sync failures leave the previous catalog untouched.

Where the catalog goes depends on `KNOWLEDGE_STORE_BACKEND`. With `local` (the default), the generated `data/github_catalog.json` is written atomically and ignored by Git. With `redis`, the catalog and README content are written to `REDIS_URL` so a serverless deployment can read them; run the sync from your machine with the production `REDIS_URL` in `backend/.env`. Pass `--catalog path.json` to force a local file in either mode. The `/projects` response strips documentation bodies and returns public metadata, featured flags, sync time, and validated HTTP(S) links.

## Vercel deployment

Vercel's filesystem is read-only, so set these environment variables on the backend project:

- `SESSION_STORE_BACKEND=redis` for chat sessions
- `KNOWLEDGE_STORE_BACKEND=redis` for the GitHub catalog and synced README content
- `REDIS_URL` for your Redis instance (for example, Upstash)
- `CORS_ORIGINS` set to the frontend's deployed URL

The `/chat` and `/projects` endpoints both read the catalog from the configured backend.

## Tests

Run focused synchronization tests:

```powershell
uv run --project backend --directory backend python -m unittest discover -s tests -v
```

The tests use mocked GitHub responses and do not require a token or network access.
