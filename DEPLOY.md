# Deploying Setu (Render + Supabase, free)

Setu runs as one Docker web service on Render, backed by a managed Postgres on
Supabase. Opt-ins keep coming through the Google Form. No secrets live in git.

## 1. Database — Supabase
1. Create a free project at https://supabase.com (set + save a DB password).
2. Click **Connect** → **Session pooler** → copy the string. It looks like:
   ```
   postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```
   Use the **Session pooler** (port 5432), not Direct (IPv6-only) or Transaction (6543).
3. Rewrite it for our driver + SSL:
   ```
   postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   ```
   This is your `DATABASE_URL`.

## 2. Push the code to GitHub
Render deploys from a Git repo. `.env`, `*.db`, `linkedin_token.json` and `resumes/`
are gitignored, so no secrets are pushed.
```bash
git init && git add -A && git commit -m "Setu"
# create an empty GitHub repo, then:
git remote add origin git@github.com:<you>/setu.git
git push -u origin main
```

## 3. Create the Render service
1. https://render.com → **New → Blueprint** → pick your repo (it reads `render.yaml`),
   or **New → Web Service** → select the repo (Docker is auto-detected).
2. Plan: **Free**.
3. Set the env vars (Dashboard → Environment):
   - `DATABASE_URL` = the Supabase string from step 1
   - `GEMINI_API_KEY` = your Gemini key
   - `OPTIN_URL` = your Google Form link
   - `GOOGLE_FORM_CSV_URL` = your published responses-CSV link
   - (`LLM_PROVIDER=gemini`, `SCHEDULE_POSTS`, `POST_INTERVAL_DAYS` come from `render.yaml`)
4. **Create / Deploy**. First boot runs `create_all` and builds the schema in Supabase.

## 4. You're live
Your app is at `https://<service-name>.onrender.com` (free HTTPS). The dashboard,
JD parsing, matching, drafting, and Google-Form import all run there.

## Notes / free-tier caveats
- **Sleeps when idle** (~15 min): first request after a quiet spell is a slow cold
  start, and the hourly auto-import only runs while the service is awake. Fine for a
  solo admin tool; you can also click **Import opt-ins** on demand.
- **No persistent disk on free**: app-uploaded resumes and the LinkedIn token reset on
  redeploy. The database (Supabase) is durable; only those local files aren't. Add a
  paid disk or external storage later if you need them.
