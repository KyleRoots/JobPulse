# AGENTS.md

## Cursor Cloud specific instructions

Scout Genius™ is a single Flask (Python) monolith for recruitment automation (XML job-feed
generation, Bullhorn ATS sync, AI candidate vetting, inbound email, public application forms).
Everything runs in one process (`main:app`); background jobs run in-process via APScheduler.
There is no separate frontend build — templates are server-rendered Jinja2 + Bootstrap.

Standard install/run commands live in `README.md`, `.github/workflows/test.yml`, `railway.toml`,
and `scripts/post-merge.sh`. The notes below only capture what is non-obvious.

### Environment / dependencies
- The update script installs into a project virtualenv at `.venv`. Always run Python via
  `.venv/bin/python` / `.venv/bin/gunicorn` / `.venv/bin/pytest`.
- `requirements.txt` is missing three runtime deps that the app imports (`matplotlib` is imported
  at module load by `reports/generate_perf_report.py`, so the app will NOT boot without it;
  `pandas` and `openpyxl` back the Excel/report features). The update script installs all three
  in addition to `requirements.txt`. `pyproject.toml`/`uv.lock` list the full set if you need it.

### Running the app (development)
- You MUST set `APP_ENV=development` (or `ENVIRONMENT=development`). With the default (production)
  env and an empty database, boot HARD-FAILS: the fresh-prod-DB seed guard raises
  `FreshProductionDatabaseError` and production seeding also demands `BULLHORN_*` secrets.
- No `DATABASE_URL` → the app falls back to SQLite at `instance/fallback.db`. On boot it runs
  `db.create_all()` + idempotent seeding, which creates all ~63 tables and a dev admin user.
  No manual migration/seed step is needed for local dev (Alembic targets Postgres only).
- Dev admin credentials: username `admin`, password `admin123` (dev fallback from
  `seeding/config.py`; override with `ADMIN_PASSWORD`).
- Run: `APP_ENV=development SESSION_SECRET=dev .venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 1 --timeout 300 main:app`
  (single worker keeps the APScheduler lock simple). `python main.py` also works (dev server, default port 5001).
- Missing external creds (Bullhorn/OpenAI/SendGrid/Microsoft Graph) are expected: those paths
  degrade gracefully and the scheduler logs auth errors without crashing.

### Browser login gotcha (HTTPS required)
- `SESSION_COOKIE_SECURE=True` is hardcoded (`extensions.py`), so the session cookie is only sent
  over HTTPS. Logging in through a browser over plain `http://localhost` will silently fail to
  persist the session (you bounce back to `/login`). To test the UI, run gunicorn with TLS, e.g.
  add `--certfile <cert> --keyfile <key>` (a self-signed cert is fine) and use `https://localhost:5000`.
- CSRF (Flask-WTF) also does strict `Referer` checking on HTTPS POSTs. Browsers send `Referer`
  automatically; a scripted client must send a matching `Referer` header (and fetch the
  `csrf_token` from the login form) or POSTs return HTTP 400.

### Tests / lint
- Run tests per-file/module (e.g. `.venv/bin/python -m pytest tests/test_auth.py`). Running the
  whole `tests/` suite at once produces ~180 spurious failures/errors: the session-scoped Flask app
  shares an in-memory rate limiter, so the many `/login` POSTs across tests trip the 20/min login
  limit and return HTTP 429. Individual files pass. A few tests (e.g. `test_embedding_ab_shadow.py`)
  also depend on the `SHADOW_LOGGING_DISABLED` env var and fail unless it is set to `false`.
- No linter is configured in this repo.
