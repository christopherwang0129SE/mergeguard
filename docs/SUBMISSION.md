<!-- NAME NOT LOCKED: repo=mergeguard, deck says "Mergesolver"/"Big Boi AI".
     Pick one and find-replace before submitting. Using "MergeGuard" throughout. -->

# MergeGuard — Hackathon Submission

**One-liner:** MergeGuard is the bouncer for your codebase — an agent-based system that kills merge conflicts on Git *before they exist*, so humans and AI coding agents can build in parallel without stepping on each other. Green means go, red means talk.

**Built for:** Nebius & Tavily Hackathon 2026
**Track:** Developer tooling / Coding Agents

> ℹ️ Draft copy — grounded in what's actually built (the Sentinel predictor). Lines marked _[confirm]_ depend on how far the build gets; adjust before final submission.

---

## Inspiration

Modern teams don't build in a straight line — they build in parallel. And it's not just humans anymore: we're entering an era where a single repo can have a whole **fleet of AI coding agents** committing at once. We kept circling one question:

> *What happens when two commits touch the same code at the same time?*

Today you find out at the worst possible moment — **after** everyone has already finished, when Git throws a wall of conflict markers. Merge conflicts get treated as an accident to clean up. We realised they're actually **predictable** — and if they're predictable, they're preventable. MergeGuard was born from wanting a guard standing at the merge point who sees the collision forming and stops it before it happens.

## What it does

MergeGuard **minimises merge conflicts on Git before they exist.** It continuously watches every in-flight branch, and for each pair of branches it computes a **conflict-risk score** by diffing them at the changed-region level — same file, overlapping or adjacent lines. It surfaces that as a simple **traffic light** for every developer (or agent):

- 🟢 **Green** — clear to keep working.
- 🟡 **Yellow** — someone is working near your code; coordinate soon.
- 🔴 **Red** — you *will* collide; resolve now (or let the Merger agent handle it).

That same signal doubles as a **review-prioritisation indicator**: the pull requests most at risk of colliding bubble to the top, so maintainers review and merge them in the right order. A PM-facing dashboard turns raw branch activity into **per-developer stats and collision exposure**, so team leads can plan around hotspots instead of firefighting them.

## How we built it

- **Backend — FastAPI.** The core **Sentinel** engine is dependency-free: it shells out to Git to compute merge-bases and per-branch diffs (`git diff -U0`), parses the changed line-ranges (hunks) per file, and scores every branch pair for region overlap using an **adjacency window** — because real Git conflicts come from *adjacent* edits, not just exact overlaps.
- **Reasoning layer — Nebius Token Factory** (OpenAI-compatible API, model `nvidia/nemotron-3-super-120b-a12b`, NVIDIA Nemotron 3 Super): explains *why* two branches collide in plain English, and powers the Merger agent's semantic merges.
- **Correctness layer — Tavily:** the Merger agent's search engine. When a conflict touches a third-party library, Tavily pulls current docs so the merged code is correct against *today's* API — not just syntactically stitched together.
- **Frontend — dashboard:** branch graph, conflict-risk heatmap, and each developer's live traffic light. _[confirm scope]_
- **Validation:** an end-to-end test builds a throwaway repo with intentionally overlapping branches and asserts the Sentinel flags them and ranks the riskiest pair first.

## Challenges we ran into

- **"Same file" ≠ "conflict."** Two people editing opposite ends of a 2,000-line file don't conflict; two editing adjacent lines do. Matching *real* Git behaviour meant parsing hunk ranges and adding an adjacency window, not just intersecting file lists.
- **The concurrency question.** Reasoning about "two commits at the same time" pushed us to model risk over diffs against a shared **merge-base**, rather than raw commit order.
- **Environment friction.** Python 3.9 wouldn't evaluate modern type syntax at runtime; we resolved it with a compatibility shim instead of losing a day to a version fight — ironically, avoiding our own kind of conflict.
- **Building in parallel without conflicting on ourselves.** We froze a shared, additive **API schema** as the contract so frontend, integration, and engine work could proceed independently — eating our own dog food.

## Accomplishments that we're proud of

- A **working predictor**, not a mock — point it at any Git repo and it returns ranked, explainable conflict-risk scores with human-readable rationales.
- Turned a vague pain ("merge hell") into a crisp, quantified **traffic-light signal** any developer understands in half a second.
- A clean agent architecture — **Sentinel → Planner → Merger** — where each piece is independently useful.
- Designed to work **for both humans and AI agents**, future-proofed for the multi-agent development wave.

## What we learned

- Merge conflicts are a **prediction problem**, not just a resolution problem — and prediction is where most of the value (and the wow) lives.
- The hard part of "resolve with an LLM" isn't the merge, it's **verifying correctness** — which is exactly why grounding the model with Tavily doc lookups plus running the tests matters.
- A shared, additive **API contract** is the cheapest way to let a team build in parallel — the very lesson our product teaches.

## What's next for MergeGuard

- **Planner agent:** proactively decompose incoming work into non-overlapping "zones" and recommend the optimal merge/rebase order.
- **Merger agent:** full semantic 3-way resolution with Tavily-grounded correctness and test validation.
- **Symbol / AST-level scoring** (tree-sitter): "both edited `login()`" should outrank "same file, far apart."
- **Delivery as a plug-in and an MCP server** (see below).

---

## Main idea: delivery as a plug-in

The winning distribution is a **VS Code extension + a GitHub App**:

- The extension shows each developer their live traffic light **right in the editor status bar**, and warns the moment they start editing a hot region.
- The GitHub App posts the risk score as a **PR check** and orders the review queue.
- The FastAPI service is the shared backend for both — the plug-in is a thin client.

Demo optics: a red dot lights up in VS Code the instant a teammate touches your function.

## MCP idea

Expose MergeGuard as an **MCP (Model Context Protocol) server** so any AI coding agent — Claude, Cursor, etc. — can call it as a **tool** before and while it edits:

- `check_conflict_risk(branch)` → returns the traffic light + rationale.
- `claim_zone(files)` → soft-locks a region so other agents route around it.

A fleet of agents working one repo can then **self-coordinate**: each checks MergeGuard, sees a red light, and picks a different file or waits. This directly delivers the tagline — *"AI agents to minimise merge conflicts"* — because the agents themselves become MergeGuard-aware. It's the natural fit for **Nebius** (the agents) + **Tavily** (grounding) + **MCP** (the coordination layer).

---

## How we meet the judging criteria

- **Nebius Cloud + Token Factory** — LLM-powered conflict explanations and the Merger agent's semantic merges run on Nebius Token Factory (`nvidia/nemotron-3-super-120b-a12b`); the service can be hosted on Nebius Cloud. _[confirm hosting]_
- **Tavily as a search engine** — the Merger agent uses Tavily to fetch current library docs so merges stay correct against live APIs. At least one demo moment visibly triggers a Tavily search.

## Demo script (the money shot)

1. Seed a repo with three branches editing the same function.
2. Open the dashboard — watch a light flip 🟢 → 🔴 in real time as the branches diverge.
3. MergeGuard names *why* they collide (Nebius) and the Merger agent proposes a resolution, checking the library's docs (Tavily).
4. Tests pass on the merged result. Ship in parallel, merge in peace.

---

## Team

- Christopher Wang ([@christopherwang0129SE](https://github.com/christopherwang0129SE))
- [@lamapony](https://github.com/lamapony)
- [@subbubhat99](https://github.com/subbubhat99)

**Repo:** https://github.com/christopherwang0129SE/mergeguard
