# MergeGuard

**An agent-based system that minimises merge conflicts.**
Built for the **Nebius & Tavily Hackathon 2026**.

Naive parallel development — whether by humans or a fleet of AI coding agents —
collides. MergeGuard watches in-flight branches and works to **predict**, **prevent**,
and **resolve** merge conflicts before they cost anyone a day.

## The three agents

| Agent | Role | Status |
|-------|------|--------|
| **Sentinel** | *Predict* — scores conflict risk between every pair of in-flight branches from git hunk overlap, then optionally refines the riskiest pairs with NVIDIA Nemotron on Nebius Token Factory. | ✅ MVP |
| **Planner** | *Prevent* — decomposes work into non-overlapping "zones" and suggests merge/rebase order. | ⏳ planned |
| **Merger** | *Resolve* — semantic 3-way merge; uses **Tavily** to check current library docs, runs tests, explains the resolution. | ⏳ planned |

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

### Nemotron risk refinement

With no API key, Sentinel is unchanged: each pair's `score`, `level`, and
`rationale` come from git hunk overlap (including a 3-line adjacency window).
The response adds `score_source: "git"` and `fallback_reason: "no_api_key"`.

To ask NVIDIA Nemotron 3 Super as well, copy `.env.example` to `backend/.env`
and set:

| Variable | Role |
|----------|------|
| `NEBIUS_API_KEY` | Token Factory key. Never commit it. An empty key disables the model. |
| `NEBIUS_BASE_URL` | Default `https://api.tokenfactory.nebius.com/v1/` (Nebius Token Factory, OpenAI-compatible). |
| `NEBIUS_MODEL` | Default `nvidia/nemotron-3-super-120b-a12b`. |
| `NEBIUS_ENABLED` | `false` forces the git scorer even when a key is set. |
| `NEBIUS_TIMEOUT_SECONDS` | One chat-completions attempt. The client does not retry. Default 40. |
| `NEBIUS_MAX_PAIRS` | Cap on model calls per `/api/analyze`. The highest git scores are sent first. Default 6. |

For each selected pair the model receives a bounded prompt: both branches'
changed files, overlapping or adjacent hunks, a trimmed diff excerpt, and a
few recent commit subjects. It must reply with JSON
`{"risk_score", "risk_level", "rationale"}`.

The published `score` is `0.5 * git_score + 0.5 * risk_score`. If the hunks
overlap, that blend cannot drop below the git score. `level` follows the
published score (high ≥ 70, medium ≥ 40, low > 0). `score_source` is `"model"`,
`rationale` is the model's rationale, and `git_score` / `git_rationale` keep
the heuristic. `risk_model` on the response is the model id when any pair used
it.

If the call errors, times out, or returns something that is not that JSON, the
pair keeps the git score, `score_source` stays `"git"`, and `fallback_reason`
is `"model_error"` or `"unparsable"`. Pairs past the cap use `"pair_cap"`.
`NEBIUS_ENABLED=false` uses `"disabled"`. The HTTP request still succeeds.

## Tests

```bash
cd backend && source .venv/bin/activate && pytest -q
```

The test suite builds a throwaway git repo with intentionally overlapping branches
and asserts the Sentinel flags them.

## Repo layout

```
backend/            FastAPI service
  app/
    main.py         entrypoint + CORS
    config.py       env-driven settings (Nebius/Tavily keys)
    schemas.py      API models
    api/routes.py   /api/health (status, model, provider), /api/analyze
    services/
      git_service.py  pure-git diff/branch helpers (subprocess)
      nemotron.py     Token Factory chat-completions client
      sentinel.py     conflict-risk scoring (git, optional Nemotron blend)
  tests/
frontend/           dashboard (to be scaffolded)
docs/               customer discovery + notes
```

## Why "MergeGuard"

Something is always on guard at the merge point — watching every in-flight
branch so a conflict never lands as a surprise.
