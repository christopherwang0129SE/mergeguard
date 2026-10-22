# Hosted demo

The click-ready list for Dima (hosting) is [DEPLOY_CHECKLIST.md](DEPLOY_CHECKLIST.md): approval gate, Railway Free and Hobby numbers, variables, smoke test, and how to turn it off. Nothing in either file creates a project or spends money.

The public demo is two projects, both named `mergeguard-demo`.

- Vercel serves the Vite dashboard from `frontend/`.
- Railway runs the API from the repo-root `Dockerfile`.

Nothing in this file deploys by itself. Create the projects in the dashboards, or with the CLIs, using the settings below. Do not put API keys in git, in the Docker image, or in Vercel.

## Image layout

The Dockerfile is at the repository root because the build context has to include both `backend/` and `demo/`. Railway's root directory for this service is the repository root, not `backend/`.

| Path in the container | What it is |
| --- | --- |
| `/app/backend` | Working directory. `uvicorn app.main:app` runs here. |
| `/app/demo/checkout-demo` | Git repo from `python3 demo/make_demo_repo.py` at image build. |
| `/app/demo/api-demo` | Git repo from `python3 demo/make_api_demo_repo.py` at image build. |

`app/services/verifier.py` sets `DEMO_ROOT` to `Path(__file__).resolve().parents[3] / "demo"`. From `backend/app/services/verifier.py` that parent is the directory that contains `backend/`. In this image that is `/app`, so `DEMO_ROOT` is `/app/demo`. Verify-by-test and branch tests already allow that folder. Leave `VERIFY_ALLOWED_ROOTS` empty unless you move the files. If you do, set `VERIFY_ALLOWED_ROOTS=/app/demo` (colon-separated if you add more roots).

The image installs `git`, the backend `requirements.txt`, and `requests`. `pytest` and `pydantic` are direct requirements. `requests` also arrives through `tavily-python`; the Dockerfile installs it explicitly so the API demo still imports it if that dependency goes away. Generated tests run with the image Python, which has pytest. The process runs as uid 1000 (`app`). `git config --system safe.directory` is `*`, so git will read the demo repos and the temporary worktrees.

The image sets `DEMO_MODE=true`. The application default, used by a local `uvicorn` with no env override, stays `false`.

The process listens on `0.0.0.0` and `${PORT:-8000}`. Railway injects `PORT`.

## Railway

1. Create a project named `mergeguard-demo`. Stay on the Free plan unless a paid plan was approved. Hobby is the paid plan. Numbers are in the cost section and in [DEPLOY_CHECKLIST.md](DEPLOY_CHECKLIST.md).
2. Add a service from this GitHub repository. Use the branch that contains the Dockerfile. Root directory is the repository root. Builder is Dockerfile (`railway.json` sets this, plus healthcheck `/api/health`, restart policy `ON_FAILURE` with 10 retries, and `sleepApplication`).
3. `railway.json` enables Serverless (the setting Railway used to call app sleeping). You can confirm it under the service's Settings, Deploy, Serverless. A slept service accrues no compute. Railway sleeps it after several minutes with no outbound traffic (their docs describe about 10 minutes). This API does not poll or hold a database connection, so an idle demo can sleep. The first request after sleep may take a few seconds and may return 502 while the container starts. Retry that request. A new deploy is required before a sleep change applies to the running container.
4. Generate a public HTTPS domain under the service networking settings. Generated domains look like `https://<name>.up.railway.app`. Copy that origin with no path and no trailing slash. That value is `VITE_API_BASE_URL` on Vercel.
5. Set variables on the service. Railway marks the keys as secrets. Do not paste them into the repo.

| Variable | Value |
| --- | --- |
| `NEBIUS_API_KEY` | Dedicated low-balance Token Factory key. Never the dev key. Empty keeps git-only scores. |
| `TAVILY_API_KEY` | Tavily key. Empty skips library-doc lookup. The API demo is the one that searches. |
| `DEMO_MODE` | `true`. Also the image default. `POST /api/analyze` then accepts only `/app/demo/checkout-demo` and `/app/demo/api-demo`. A `repo_url`, or any other path, returns 403 and is not cloned. |
| `CORS_ALLOW_ORIGINS` | The Vercel origin, for example `https://mergeguard-demo.vercel.app`. Comma-separated if you have more than one. No trailing slash. |
| `CORS_ALLOW_ORIGIN_REGEX` | Optional. Leave empty until you want preview deployments. Example: `https://mergeguard-demo.*\.vercel\.app`. The regex must match the whole origin. |
| `VERIFY_ALLOWED_ROOTS` | Leave unset with this image. |

Leave `GITHUB_REPOSITORY`, `GITHUB_TOKEN`, and `GITHUB_SEATED_USERS` unset. The GitHub sync route is unchanged by demo mode, and it clones whatever repository those variables name.

CORS is read when the process starts. Change the variables, then redeploy.

Set a 512 MB memory limit in the service settings. That is the Free plan maximum (1 vCPU and 0.5 GB RAM per service). Measured locally on this branch, the demo-mode API idles near 75 MB RSS, and both bundled demos finish branch tests in a few seconds with no API keys. On Hobby you can raise memory if a deploy is killed during verify-by-test or branch tests. You do not need a large vCPU reservation for a demo. Free will not accept a limit above 0.5 GB or 1 vCPU.

### Cost

Published Railway prices as of October 2026:

| Plan | Subscription | Included usage | Caps and meter |
| --- | --- | --- | --- |
| Free | $0 | $1/month | Max 1 vCPU and 0.5 GB RAM per service. App sleeping. |
| Hobby | $5/month | $5 of usage | Past the included $5: RAM $10/GB-month, CPU $20/vCPU-month, egress $0.05/GB. |

Those resource rates apply on both plans. A service held at 0.5 GB around the clock is about $5 of RAM before CPU. That spends the Hobby credit on its own. It is also well above Free's $1 credit, so an always-on service does not fit Free. Sleeping is what keeps a quiet demo inside either credit. In Workspace Settings → Usage, set a hard usage limit and an email alert before the service is public. A limit of $5 matches Hobby's included usage. On Free the included credit is $1, so a higher limit is a spending-limit change and needs approval. Egress for this demo is the JSON the dashboard fetches. Tavily and Token Factory bill separately from Railway. Token Factory is prepaid: the cap is the balance on a dedicated key. Tavily draws its own free-tier credits.

## Vercel

The Hobby plan is free for non-commercial use. Put no secrets on Vercel. Spend Management does not apply on Hobby.

1. Create a project named `mergeguard-demo`. Import this repository.
2. Set the root directory to `frontend`. Framework preset Vite. `frontend/vercel.json` sets the framework, `npm run build`, output `dist`, and an SPA rewrite to `/index.html`. Vercel serves files that exist (the JS and CSS bundles) and rewrites other paths to `index.html`.
3. Set these environment variables for Production and, if you use previews, for Preview. They are baked in at build time by Vite. Changing them requires a new deployment. Do not put `NEBIUS_API_KEY` or `TAVILY_API_KEY` here.

| Variable | Value |
| --- | --- |
| `VITE_API_BASE_URL` | The Railway origin, with no trailing slash. `src/services/api.ts` uses this and otherwise calls `http://localhost:8000`. An empty value also falls back to localhost, so the variable has to be non-empty. |
| `VITE_DEMO_REPO_PATH` | `/app/demo/checkout-demo` |
| `VITE_API_DEMO_REPO_PATH` | `/app/demo/api-demo` |

`vite.config.ts` reads the two demo paths at build time. An unset variable keeps a path resolved on the build machine (`../demo/checkout-demo` and `../demo/api-demo` next to `frontend/`). That path does not exist in the Railway container, so the hosted build must set both. An empty value hides that dashboard button. The browser sends the string to `POST /api/analyze` as `repo_path`. It is a path inside the API container, not a path on Vercel.

4. Deploy. Open the Vercel URL, then the site's Railway health URL (`https://<railway-host>/api/health`). The dashboard's demo buttons should analyze the two container paths. With keys unset, the result is git-only. With keys set, the checkout demo still does not call Tavily (standard library only) and the API demo does.

If the browser blocks the request, the Vercel origin is missing from `CORS_ALLOW_ORIGINS` or the regex does not full-match it. Redeploy Railway after fixing the variable.

## Local check

From the repository root, on a machine with Docker. This uses port 8090 so it does not touch a dev server on port 8000.

```bash
docker build -t mergeguard-demo .
docker run --rm -d --name mergeguard-demo-local -p 8090:8090 \
  -e PORT=8090 -e DEMO_MODE=true mergeguard-demo
curl -sS http://127.0.0.1:8090/api/health
curl -sS -X POST http://127.0.0.1:8090/api/analyze \
  -H 'content-type: application/json' \
  -d '{"repo_path":"/app/demo/checkout-demo"}'
docker stop mergeguard-demo-local
```

Do not pass `NEBIUS_API_KEY` or `TAVILY_API_KEY`. Do not mount `backend/.env` into the container. Health should report `nebius_ready` and `tavily_ready` as false. The analyze call should come back with `score_source` `git` and `usage.llm_calls` 0. On the checkout demo the raw git score stays low. Branch tests use no model, and when the branches' own tests fail on the merge they raise the published score. A `repo_url` to the same port should return 403.

Without Docker, copy `backend/` and `demo/` into a temp directory that keeps that layout, run the two `demo/make_*_demo_repo.py` scripts there, and start uvicorn from the copied `backend/` on port 8090 with `DEMO_MODE=true` and no keys. `DEMO_ROOT` follows the copy, the same way it follows `/app` in the image.
