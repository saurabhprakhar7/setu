# Setu — Recruiting Automation

Open-source, self-hosted system for a **solo recruiter** placing **senior engineers** into **remote contract roles** (3–6 months). Clients send a JD + pay commission on placement. We source candidates, get their opt-in, match them to JDs, and forward a shortlist to the client.

> Setu means "bridge" (Sanskrit) — it connects engineers to the right roles.
> This file is the spec **and** the build plan for implementing the project with Claude Code.

## Working agreement (for Claude Code)

- Implement in the **Build order** below, **one phase at a time**. After each phase, make sure it runs, then stop for review before starting the next.
- Keep it minimal and dependency-light. **Ask before adding any new dependency or any paid/cloud service.**
- Secrets (SMTP creds, API keys, etc.) live in `.env` — never in code. Keep a `.env.example` updated.
- Prefer plain, readable code over cleverness. Small functions, clear names.

## Non-negotiable rules (compliance — never violate)

These shape every feature. If a request breaks one, **stop and flag it** instead of building it.

- **No LinkedIn scraping, auto-connect, or auto-DM.** The app may *draft* and *track* LinkedIn outreach, never *send* it. LinkedIn sending stays manual and human-paced.
- **No cold messaging.** Email/WhatsApp automation runs **only** on opted-in candidates.
- **Consent is mandatory.** Every candidate must have `consent = true` + `consent_date` before any automated message (DPDP record).
- **WhatsApp:** business-initiated needs prior opt-in + a Meta-approved template. Manual Business-app broadcast is the fallback for now.
- **Never build anything whose purpose is to circumvent a platform's Terms of Service.**

> **Sanctioned exception (Phase 8):** auto-posting to the recruiter's *own* LinkedIn profile via the official API, with approve-before-publish by default. This is "post as the authenticated member", not auto-connect/auto-DM, and does not circumvent ToS.

## Stack (open-source / free / self-hosted only)

- Backend: **Python + FastAPI**
- ORM/DB: **SQLModel** over **Postgres** (Supabase, cloud) via `DATABASE_URL`; falls back to **SQLite** when unset
- Deployment: **Docker** — single `web` service (FastAPI) → Supabase Postgres; hosted on **Render** (see `DEPLOY.md`)
- LLM: provider-configurable via `LLM_PROVIDER` — **Gemini** (`gemini-2.5-flash`, default) or local **Ollama** (`qwen2.5`)
- Templating/UI: Jinja2 + minimal vanilla HTML/CSS (no heavy frontend framework)
- Email: **SMTP** (config via env) — the primary automated channel
- Resume storage: local dir (`RESUMES_DIR`, a Docker volume); MinIO optional later
- WhatsApp: manual for now (text generated, user sends); opted-in Business API later

## Project structure

```
setu/
  app/
    main.py          # FastAPI app + routes
    models.py        # SQLModel: Candidate, JD, Message
    db.py            # engine + session
    llm.py           # parse_jd(), draft_message()  (Ollama)
    matching.py      # score_candidate(candidate, jd)
    email_sender.py  # SMTP send
    templates/       # opt-in form, dashboard (Jinja2)
    static/
  resumes/           # uploaded files (gitignored)
  Dockerfile
  docker-compose.yml # single web service → external Postgres (Supabase)
  .dockerignore
  .env.example
  requirements.txt
  README.md
  CLAUDE.md
```

## Commands

- Docker (recommended): `cp .env.example .env` (set `GEMINI_API_KEY`) then `docker compose up --build` → http://localhost:8000
- Local (SQLite): `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && uvicorn app.main:app --reload`
- Ollama (only if `LLM_PROVIDER=ollama`): `ollama serve` then `ollama pull qwen2.5`
- (Tests come later: `pytest`)

## Data model

**Candidate:** `id`, `name`, `email`, `whatsapp`, `skills[]`, `role_type` (frontend/backend/fullstack), `seniority`, `years_experience`, `segment` (active/passive/freelance), `current_pay`, `expected_pay`, `location`, `remote_ok`, `availability`, `resume_path`, `consent`, `consent_date`, `status` (sourced → contacted → opted_in → resume → sent_to_client)

**JD:** `id`, `role`, `stack[]`, `min_years_experience`, `max_years_experience`, `seniority`, `location`, `remote`, `contract_duration`, `rate`, `client`, `source`

**Message:** `id`, `candidate_id`, `jd_id`, `channel` (email/whatsapp), `body`, `sent_at`, `status`

## Matching logic (candidate ↔ JD)

Score, in order of weight:
1. **Skill overlap** with JD `stack` (heaviest).
2. **Years of experience** — the JD always states this, so it's a hard-ish filter: a candidate below `min_years_experience` is dropped or heavily penalized. Default target is 5+ years.
3. Seniority match, `remote_ok`/location fit, contract willingness, pay fit (`expected_pay` ≤ JD `rate`).

`llm.parse_jd()` MUST extract `min_years_experience` / `max_years_experience` as structured fields.

## Build order (one phase per session, verify after each)

1. **Scaffold** — FastAPI app, SQLite via SQLModel, `Candidate` + `JD` models, `.env.example`, `requirements.txt`. Confirm `uvicorn` serves a health route.
2. **Opt-in form** — public page collecting all candidate fields + resume upload (→ `resumes/`), consent checkbox → sets `consent` + `consent_date`. On submit: create candidate, `status = opted_in`.
3. **JD intake + parser** — endpoint to paste a JD → `llm.parse_jd()` returns structured JD incl. experience years → save `JD` row.
4. **Matcher** — `matching.score_candidate()`; endpoint: given a JD id, return ranked opted-in candidates with matched/missing skills and the experience check.
5. **Outreach** — `llm.draft_message()` per candidate (email + WhatsApp text, segment-aware); `email_sender` sends email to opted-in matches; WhatsApp text shown for manual send. Log to `Message`.
6. **Client shortlist** — compile matched + interested candidates into a CSV / paste-ready text block for the client WhatsApp group.
7. **Dashboard** — one page: pool, JDs, matches, statuses; plus a manual "add sourced candidate" form (for the pull side) with draft + status tracking. No LinkedIn automation here.
8. **LinkedIn auto-posting (optional module)** — draft a post from a JD with the LLM, approve-before-publish, then publish to the recruiter's *own* profile via LinkedIn's official API. Optional; Setu works fully without it. See Phase 8 below.

## Pipeline

opt-in form → pool (consent) → JD in (paste/email) → parse (Ollama) → match pool → auto-message opted-in matches → resume already on file → shortlist → client WhatsApp group. Everything from "JD in" onward is automated; only sourcing candidates into the pool is human-paced.

## Target candidates / segments

Senior (5+ yrs) Frontend (React/Angular/Vue) + Backend (Node/Java/Python/Go), India, remote contract. Three segments drive different message hooks in `draft_message()`:
- **active** (laid off) → direct hook.
- **passive** (underpaid) → private, higher-pay-+-remote hook.
- **freelance** → minimal, just-the-role hook.

## Phase 8 — LinkedIn auto-posting (optional module)

Drafts a LinkedIn post from a JD with the LLM, lets the recruiter review/approve it, then publishes to their **own** profile via LinkedIn's official API. Powers the inbound / audience-building engine. **Optional** — Setu works fully without it.

**Why allowed:** posting to your *own* profile through the official API is "post on behalf of the authenticated member", not the banned auto-connect / auto-DM. Two rules keep it clean: (1) **approve before publish** by default (`LINKEDIN_AUTOPOST=false`); (2) **sane cadence** — 2–3 posts/week.

**One-time setup:** create a LinkedIn Developer app (must be linked to a Company Page even for personal posting); add **Share on LinkedIn** (`w_member_social`) + **Sign In with LinkedIn (OIDC)**; run 3-legged OAuth once with `openid profile w_member_social` → access + refresh tokens; fetch your member id and build `urn:li:person:{id}`.

**Tokens & limits:** access token ~60 days, refresh ~365 days → implement refresh. Rate limit ~100 calls/day/member (irrelevant at 2–3/week).

**Config (.env):** `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_REFRESH_TOKEN`, `LINKEDIN_PERSON_URN`, `LINKEDIN_AUTOPOST=false`.

**Data model — Post:** `id`, `jd_id` (nullable), `body`, `status` (draft/approved/published/failed), `linkedin_urn` (set after publish), `error` (nullable), `created_at`, `published_at`.

**Module layout:** `llm.draft_post(jd_or_prompt)` (brand voice, ~80–120 words, ends with the opt-in form link); `app/linkedin.py` — `get_access_token()` (refreshes if expired), `publish_post(text) -> urn` (Posts API, `author = LINKEDIN_PERSON_URN`, visibility `PUBLIC`, expect `201`, read URN from `X-RestLi-Id`); a "Posts" page with Approve / Edit / Discard.

**Flow:** JD in (or manual prompt) → `draft_post()` creates a `draft` Post → dashboard shows it → recruiter edits + Approves (or Discards) → on Approve, publish (on click if `AUTOPOST=false`, else automatically), store `linkedin_urn`, set `published`. On API/token error → `failed` + `error`, show with Retry.

**Fallback (no OAuth yet):** `draft_post()` still works — show the text with a **Copy** button to paste into LinkedIn manually. The API publish is an enhancement, not a dependency.

**Build steps (one at a time, verify after each):**
1. `draft_post()` + Posts page showing drafts with a Copy button. Works with **no** LinkedIn API.
2. OAuth + token storage + refresh in `app/linkedin.py`.
3. `publish_post()` + wire Approve → publish + status/error handling.
4. (Optional) cadence guardrail — warn if posting more than ~3×/week.

## Status

Nothing scaffolded yet — this file is the spec for the Claude Code build. A v1 prototype existed only as a standalone HTML proof-of-concept (in-browser storage); the self-hosted FastAPI app described here replaces it.
