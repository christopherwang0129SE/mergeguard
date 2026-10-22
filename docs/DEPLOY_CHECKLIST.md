# Hosting checklist

For Dima, who owns hosting. Tick in order. This file does not create a project, enter a secret, or spend money. Image layout and the local Docker check are in [DEPLOY.md](DEPLOY.md).

Railway prices below are the published figures as of October 2026.

## 1. Needs Dima's approval before anyone clicks

Stop until Dima has approved the item you are about to click.

- [ ] Creating the Vercel project
- [ ] Creating the Railway project
- [ ] Entering secrets (`NEBIUS_API_KEY`, `TAVILY_API_KEY`)
- [ ] Any paid plan (Railway Hobby costs money; Vercel Hobby is free for non-commercial use)
- [ ] Any spending-limit change (Railway usage limit, Token Factory balance, or any other cap)

## 2. Railway (backend)

Repo-root `Dockerfile` and `railway.json`. The service root is the repository root, not `backend/`. `railway.json` selects the Dockerfile builder, healthcheck `/api/health`, restart policy `ON_FAILURE` with 10 retries, and `sleepApplication` (Serverless).

Both plans need the project-creation approval in section 1. Hobby also needs the paid-plan approval.

| Plan | Pay | Included | What you get |
| --- | --- | --- | --- |
| Free | $0 | $1/month usage credit | Max 1 vCPU and 0.5 GB RAM per service. App sleeping. |
| Hobby | $5/month | $5 of usage | Past that, RAM $10/GB-month, CPU $20/vCPU-month, egress $0.05/GB. |

Measured locally on this branch: demo-mode API idle RSS is about 75 MB, and both bundled demos finish analysis, including branch tests, in a few seconds. That run was git-only, with no API keys. Use a 512 MB memory limit. That is the Free plan maximum, and it covers this demo. The same resource rates draw down both credits. Holding 0.5 GB all month is about $5 of RAM before CPU, which spends the Hobby credit and is more than Free's $1. Leave Serverless on.

- [ ] Create project `mergeguard-demo` on Free, unless Hobby was approved
- [ ] Add a service from this GitHub repository, on the branch that contains the Dockerfile. Root directory: repository root. Builder: Dockerfile
- [ ] Confirm Settings → Deploy → Serverless is on. A slept service accrues no compute. Railway sleeps it after several minutes with no outbound traffic (about 10 minutes). The first request after sleep can return 502 once while the container starts; retry it
- [ ] Set the memory limit to 512 MB. Leave the CPU limit alone. Free cannot go above 0.5 GB or 1 vCPU
- [ ] Workspace Settings → Usage: set a hard usage limit and an email alert. $5 matches Hobby's included usage. On Free the included credit is $1, so a limit above $1 is a spending-limit change (section 1)
- [ ] Generate a public HTTPS domain. Copy the origin with no path and no trailing slash (`https://<name>.up.railway.app`). That value is `VITE_API_BASE_URL`
- [ ] Set variables. Mark the keys as secrets. Redeploy after any change; CORS is read when the process starts

| Variable | Value |
| --- | --- |
| `NEBIUS_API_KEY` | Secret. A dedicated Token Factory key whose prepaid balance is low. Never the dev key. Empty keeps git-only scores. |
| `TAVILY_API_KEY` | Secret. Optional. Empty skips library-doc lookup. |
| `DEMO_MODE` | `true`. The image defaults to this as well. |
| `CORS_ALLOW_ORIGINS` | The Vercel origin, no trailing slash. Comma-separated if there is more than one. Set this after section 3, then redeploy. |
| `CORS_ALLOW_ORIGIN_REGEX` | Optional. Leave empty until preview deployments are wanted. Example: `https://mergeguard-demo.*\.vercel\.app`. The regex must match the whole origin. |

- [ ] Leave `GITHUB_REPOSITORY`, `GITHUB_TOKEN`, `GITHUB_SEATED_USERS`, and `VERIFY_ALLOWED_ROOTS` unset. The image already allows `/app/demo`
- [ ] Confirm the deploy is healthy on `/api/health`

## 3. Vercel (dashboard)

Root directory `frontend/`. `frontend/vercel.json` sets Vite, `npm run build`, output `dist`, and the SPA rewrite to `/index.html`.

Hobby is free for non-commercial use. No secrets on Vercel. Spend Management does not apply on Hobby; skip it.

- [ ] Create project `mergeguard-demo` from this repository (section 1)
- [ ] Root directory: `frontend`. Framework preset: Vite
- [ ] Set Production variables. Vite bakes them in at build time, so a change needs a new deployment

| Variable | Value |
| --- | --- |
| `VITE_API_BASE_URL` | The Railway origin from section 2, no trailing slash. Empty makes the dashboard call `http://localhost:8000`. |
| `VITE_DEMO_REPO_PATH` | `/app/demo/checkout-demo` |
| `VITE_API_DEMO_REPO_PATH` | `/app/demo/api-demo` |

- [ ] Do not add `NEBIUS_API_KEY`, `TAVILY_API_KEY`, or any other secret
- [ ] Deploy. Copy the Vercel origin into Railway `CORS_ALLOW_ORIGINS`, then redeploy Railway

## 4. Spend caps outside Railway

Railway's usage limit does not cap these vendors.

- [ ] Token Factory is prepaid credit. Use a separate key or project, and put the cap in that balance. That key is `NEBIUS_API_KEY`
- [ ] Tavily draws its own free-tier credits when `TAVILY_API_KEY` is set. Leave the variable empty to spend nothing there

## 5. Smoke test after deploy

Use the generated hosts. A cold start after sleep may return 502 once. Retry that request.

- [ ] Health lists `nebius_ready` and `tavily_ready`. Each is true only when that key is set:

```bash
curl -sS "https://<railway-host>/api/health"
```

- [ ] On the Vercel site, **Try demo repo** and **Try API demo (Tavily)** both finish
- [ ] A `repo_url` returns 403 while `DEMO_MODE` is on:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST "https://<railway-host>/api/analyze" \
  -H 'content-type: application/json' \
  -d '{"repo_url":"https://github.com/example/not-a-demo.git"}'
```

- [ ] CORS allows the Vercel origin. `access-control-allow-origin` on this response is that origin:

```bash
curl -sS -D - -o /dev/null \
  -H "Origin: https://<vercel-host>" \
  "https://<railway-host>/api/health"
```

- [ ] After the service has slept, call health again. One 502 on wake-up is expected. The retry returns 200

## 6. Demo-mode guarantees

Verified locally on this branch. No hosting project was created for that check.

- [ ] `POST /api/analyze` accepts only `/app/demo/checkout-demo` and `/app/demo/api-demo`
- [ ] `repo_url`, and any other path, return 403 and are not cloned
- [ ] With no keys, scores stay git-only, and branch tests still confirm both demos

## 7. Rollback / turn-off

- [ ] Pause the Railway service
- [ ] Delete the Vercel deployment
- [ ] Rotate the Token Factory key and the Tavily key, then clear both variables on Railway
