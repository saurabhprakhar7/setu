# Setu

Self-hosted recruiting automation for placing senior engineers into remote contract roles.
See [CLAUDE.md](CLAUDE.md) for the full spec and build plan.

## Run with Docker (recommended)

Single container, using your Postgres (e.g. Supabase) from `DATABASE_URL`.

```bash
cp .env.example .env      # set DATABASE_URL, GEMINI_API_KEY, OPTIN_URL, GOOGLE_FORM_CSV_URL
docker compose up --build
```

- http://localhost:8000/ — recruiter dashboard (pool, JDs, statuses, add sourced candidate)
- http://localhost:8000/optin — public opt-in form (or use your Google Form)

The database is external (Supabase). Uploaded resumes + the LinkedIn token persist in the
`setu_data` volume. Stop with `docker compose down`. See [DEPLOY.md](DEPLOY.md) for hosting.

## Run locally (without Docker)

Uses `DATABASE_URL` from `.env`; leave it blank to fall back to SQLite (no DB needed).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # set GEMINI_API_KEY (and DATABASE_URL, or blank for SQLite)
uvicorn app.main:app --reload
```

LLM defaults to **Gemini** (`LLM_PROVIDER=gemini` + `GEMINI_API_KEY`); set `LLM_PROVIDER=ollama`
to use a local [Ollama](https://ollama.com) instead. Email outreach needs the `SMTP_*` vars.

Flow: candidates opt in → paste a JD (parsed by the LLM) → view ranked matches →
email opted-in matches + draft WhatsApp → compile a client shortlist (text/CSV).
