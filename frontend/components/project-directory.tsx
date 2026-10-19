"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, Search } from "lucide-react";

interface DocumentedLink {
  label: string;
  url: string;
  source_url: string;
}

interface PublicRepository {
  id: number | null;
  name: string;
  full_name: string;
  description: string | null;
  html_url: string;
  homepage: string | null;
  default_branch: string | null;
  language: string | null;
  languages: Record<string, number>;
  stargazers_count: number;
  forks_count: number;
  open_issues_count: number;
  created_at: string | null;
  updated_at: string | null;
  pushed_at: string | null;
  last_commit_sha: string | null;
  is_featured: boolean;
  documented_deployment_links: DocumentedLink[];
}

interface ProjectCatalog {
  username: string | null;
  last_synchronized_at: string | null;
  repository_count: number;
  repositories: PublicRepository[];
}

const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/$/, "");

function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

function formatSyncTime(value: string | null): string {
  if (!value) return "Not synchronized yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Last sync time unavailable";
  return `Synced ${new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date)}`;
}

export default function ProjectDirectory() {
  const [catalog, setCatalog] = useState<ProjectCatalog | null>(null);
  const [query, setQuery] = useState("");
  const [featuredOnly, setFeaturedOnly] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function loadCatalog() {
      try {
        const response = await fetch(`${API_BASE}/projects`, {
          signal: controller.signal,
        });
        if (!response.ok)
          throw new Error(
            `Project catalog request failed (${response.status}).`,
          );
        const result = (await response.json()) as Partial<ProjectCatalog>;
        if (!Array.isArray(result.repositories))
          throw new Error("Project catalog response was invalid.");
        setCatalog({
          username:
            typeof result.username === "string" ? result.username : null,
          last_synchronized_at:
            typeof result.last_synchronized_at === "string"
              ? result.last_synchronized_at
              : null,
          repository_count: result.repositories.length,
          repositories: result.repositories as PublicRepository[],
        });
        setError(null);
      } catch (loadError) {
        if (!controller.signal.aborted) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "Could not load public projects.",
          );
        }
      } finally {
        if (!controller.signal.aborted) setIsLoading(false);
      }
    }

    void loadCatalog();
    return () => controller.abort();
  }, []);

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const repositories = (catalog?.repositories ?? []).filter((repository) => {
    if (featuredOnly && !repository.is_featured) return false;
    if (!normalizedQuery) return true;
    return [
      repository.name,
      repository.description,
      repository.language,
      ...Object.keys(repository.languages),
    ].some((value) => value?.toLocaleLowerCase().includes(normalizedQuery));
  });

  return (
    <div className="project-directory">
      <div className="directory-toolbar">
        <label className="directory-search">
          <Search aria-hidden="true" size={16} />
          <span className="sr-only">Search public repositories</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search repositories or languages"
            type="search"
          />
        </label>
        <div
          className="directory-filters"
          role="group"
          aria-label="Repository filters"
        >
          <button
            type="button"
            className={
              !featuredOnly ? "directory-filter active" : "directory-filter"
            }
            aria-pressed={!featuredOnly}
            onClick={() => setFeaturedOnly(false)}
          >
            All {catalog ? `(${catalog.repository_count})` : ""}
          </button>
          <button
            type="button"
            className={
              featuredOnly ? "directory-filter active" : "directory-filter"
            }
            aria-pressed={featuredOnly}
            onClick={() => setFeaturedOnly(true)}
          >
            Featured{" "}
            {catalog
              ? `(${catalog.repositories.filter((repo) => repo.is_featured).length})`
              : ""}
          </button>
        </div>
      </div>

      <div className="directory-status" aria-live="polite">
        {isLoading && <p role="status">Loading public repositories…</p>}
        {!isLoading && error && (
          <p className="directory-message directory-error" role="status">
            Project catalog is currently unavailable. Start the API and run{" "}
            <code>python sync_github.py</code> from `backend/`.
          </p>
        )}
        {!isLoading && !error && catalog?.repository_count === 0 && (
          <p className="directory-message" role="status">
            No repository data has been synchronized yet. Run{" "}
            <code>python sync_github.py</code> from `backend/`.
          </p>
        )}
        {!isLoading && !error && catalog && catalog.repository_count > 0 && (
          <p>
            {repositories.length} repositories <span>·</span>{" "}
            {formatSyncTime(catalog.last_synchronized_at)}
          </p>
        )}
      </div>

      {!isLoading && !error && repositories.length > 0 && (
        <div className="directory-grid">
          {repositories.map((repository) => {
            const repositoryUrl = safeExternalUrl(repository.html_url);
            const homepageUrl = safeExternalUrl(repository.homepage);
            const documentedLinks = repository.documented_deployment_links
              .map((link) => ({ ...link, safeUrl: safeExternalUrl(link.url) }))
              .filter((link) => link.safeUrl);
            const languageNames = Object.keys(repository.languages ?? {}).slice(
              0,
              4,
            );
            if (languageNames.length === 0 && repository.language)
              languageNames.push(repository.language);

            return (
              <article className="directory-card" key={repository.full_name}>
                <div className="directory-card-topline">
                  <span className="directory-repo-owner">
                    {repository.full_name.split("/")[0]}
                  </span>
                  {repository.is_featured && (
                    <span className="directory-featured">FEATURED</span>
                  )}
                </div>
                <h3>{repository.name}</h3>
                <p className="directory-description">
                  {repository.description ||
                    "No description provided on GitHub."}
                </p>
                {languageNames.length > 0 && (
                  <ul
                    className="directory-languages"
                    aria-label="Repository languages"
                  >
                    {languageNames.map((language) => (
                      <li key={language}>{language}</li>
                    ))}
                  </ul>
                )}
                <div className="directory-links">
                  {repositoryUrl && (
                    <a href={repositoryUrl} target="_blank" rel="noreferrer">
                      Repository <ArrowUpRight aria-hidden="true" size={13} />
                    </a>
                  )}
                  {homepageUrl && (
                    <a href={homepageUrl} target="_blank" rel="noreferrer">
                      Homepage <ArrowUpRight aria-hidden="true" size={13} />
                    </a>
                  )}
                  {documentedLinks.map((link) => (
                    <a
                      key={`${repository.full_name}:${link.url}`}
                      href={link.safeUrl!}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {link.label} <ArrowUpRight aria-hidden="true" size={13} />
                    </a>
                  ))}
                </div>
              </article>
            );
          })}
        </div>
      )}

      {!isLoading &&
        !error &&
        catalog &&
        catalog.repository_count > 0 &&
        repositories.length === 0 && (
          <p className="directory-message" role="status">
            No repositories match this search.
          </p>
        )}
      {catalog?.username && (
        <p className="directory-source">
          Public repositories from github.com/{catalog.username}
        </p>
      )}
    </div>
  );
}
