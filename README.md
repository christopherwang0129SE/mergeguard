# MergeGuard

**An agent-based system that minimises merge conflicts.**
Built for the **Nebius & Tavily Hackathon 2026**.

Naive parallel development — whether by humans or a fleet of AI coding agents —
collides. Nordvakt watches in-flight branches and works to **predict**, **prevent**,
and **resolve** merge conflicts before they cost anyone a day.

## The three agents

| Agent | Role | Status |
|-------|------|--------|
| **Sentinel** | *Predict* — scores conflict risk between every pair of in-flight branches by diffing them at the region (and, later, symbol) level. | ✅ MVP |
| **Planner** | *Prevent* — decomposes work into non-overlapping "zones" and suggests merge/rebase order. | ⏳ planned |
| **Merger / Resolver** | *Resolve* — reproduces Git conflicts in an isolated worktree, asks Nebius for text resolutions, optionally researches docs with Tavily, and returns a patch with validation results. | ✅ MVP |

- **LLM inference:** Nebius Token Factory (OpenAI-compatible API)
- **Web research:** Tavily
- **Backend:** FastAPI (Python) · **Frontend:** dashboard (branch graph + risk heatmap)

## Quickstart (backend)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env    # fill in keys (optional for Sentinel)
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the API.

### Try the Sentinel on any repo

```bash
curl -s -X POST http://localhost:8000/api/analyze \
  -H 'content-type: application/json' \
  -d '{"repo_path": "/path/to/some/repo", "base_branch": "main"}' | python3 -m json.tool
```

Returns a per-branch diff summary and a ranked list of branch-pair **conflict-risk
scores** with human-readable rationales.

### Sync open GitHub pull requests

Set `GITHUB_REPOSITORY` to `owner/repository` and `GITHUB_SEATED_USERS` to a
comma-separated list of seated GitHub usernames in `backend/.env`. Set
`GITHUB_TOKEN` for private repositories or higher API rate limits. The
`GET /api/github/pull-requests` endpoint syncs open pull requests targeting
`DEFAULT_BASE_BRANCH`, includes only authors in the seated-user list, fetches
their GitHub PR refs into the local workspace clone, and returns the PR details
alongside Sentinel's conflict-risk analysis against the latest fetched
`origin/DEFAULT_BASE_BRANCH`. It does not assign reviewers or merge pull
requests.

## Tests

```bash
cd backend && source .venv/bin/activate && pytest -q
```

The test suite builds a throwaway git repo with intentionally overlapping branches
and asserts the Sentinel flags them.

## Resolver

`POST /api/resolve` proposes a merge of committed `source_branch` into
`target_branch` in a **local repository**. It pins both commits and uses a temporary
detached Git worktree; your checkout, uncommitted changes, branch refs, and index
are preserved. The temporary worktree is removed on completion or failure.
Nothing is committed, pushed, or applied to the original checkout.

Configure `NEBIUS_API_KEY` in `backend/.env` and select a `NEBIUS_MODEL` available
in your Token Factory account. The default endpoint is
`https://api.tokenfactory.nebius.com/v1`. Sentinel and clean merges need no API key.
If the key is missing, actual conflicts return `needs_review` with an explanation.
Conflicted file versions are sent to Nebius for inference.

Windows setup with uv, from the repository root:

```powershell
Copy-Item .env.example backend/.env
# Edit backend/.env to configure your key and model.
cd backend
uv run --no-project --with-requirements requirements.txt uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another PowerShell terminal, replace the path and branch names with a repository
containing changes you want to merge:

```powershell
$body = @{
    repo_path = 'C:\path\to\repository'
    target_branch = 'main'
    source_branch = 'feature/my-change'
    run_tests = $false
} | ConvertTo-Json
$result = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/resolve -Method Post -ContentType 'application/json' -Body $body
$result | ConvertTo-Json -Depth 10
```

The response includes pinned commit IDs, per-file explanations, validation results,
and a Git patch against `target_commit`, including source changes that merged cleanly.

| Status | Meaning |
|--------|---------|
| `clean` | Git merged without conflicts; no model was needed. |
| `proposed` | Text conflicts have a generated proposal; automatic checks passed. Review intent before applying. |
| `needs_review` | At least one conflict is unresolved or unsupported; no complete patch is returned. |
| `validation_failed` | A complete patch exists, but syntax/marker checks or configured tests failed. |

Python syntax and remaining conflict markers are checked automatically. Tests are
**skipped by default**, and their skipped status is explicit; `proposed` does not
mean tests passed or semantic correctness was proven. To enable tests, configure
`RESOLVER_TEST_COMMAND` in `backend/.env` as a JSON argv array, for example
`["python", "-m", "pytest", "-q"]`, and send `run_tests: true`. The command runs
from the analyzed repository's root, using the server environment, with a timeout.
No shell or API-supplied test command is used. Install the analyzed project's own
test dependencies and choose a command appropriate to that project.

For documentation grounding, configure `TAVILY_API_KEY` and send an optional
`research_query`, such as `"official documentation for the library API being merged"`.
The explicit query is sent to Tavily; retrieved snippets are included in Nebius
context and source URLs are returned. Research failures appear in `warnings`.

MVP limits: UTF-8 regular text files, at most 20 conflicts, and 100 KB per file
version/output. Binary, symlink, submodule, rename, and delete/modify conflicts are
left for human review. The model makes one proposal per file and can abstain;
there is no automatic retry or publication. This local developer service has no
authentication or repository allowlist: bind it to localhost and use trusted repos.
A Git worktree isolates checkout changes, **not execution**: configured tests and
repository Git filters/merge drivers run with the server's permissions.

To save a reviewable patch without changing its encoding on Windows:

```powershell
if ($result.status -in @('clean', 'proposed')) {
    $patchPath = Join-Path $PWD 'resolution.patch'
    [System.IO.File]::WriteAllText($patchPath, $result.patch, [System.Text.UTF8Encoding]::new($false))
}
```

Review the patch, then use a clean checkout at the returned `target_commit` and
run `git apply --check resolution.patch` before `git apply resolution.patch`.
Run the project's checks, commit, and push through the team's normal PR workflow.
Fetch teammates' branches first and use `origin/feature-name` when they have no
local branch. The resolver does not fetch or include uncommitted teammate edits.

Run all tests (providers are mocked; no paid API calls):

```powershell
cd backend
uv run --no-project --with-requirements requirements.txt python -m pytest -q
```

## Repo layout

```
backend/            FastAPI service
  app/
    main.py         entrypoint + CORS
    config.py       env-driven settings (Nebius/Tavily keys)
    schemas.py      API models
    api/routes.py   /api/health, /api/analyze, /api/resolve
    services/
      git_service.py  pure-git diff/branch helpers (subprocess)
      sentinel.py     conflict-risk scoring
      resolver.py     isolated merge proposals + Nebius/Tavily + validation
  tests/
frontend/           dashboard (to be scaffolded)
docs/               customer discovery + notes
```

## Why "MergeGuard"

Something is always on guard at the merge point — watching every in-flight
branch so a conflict never lands as a surprise.
