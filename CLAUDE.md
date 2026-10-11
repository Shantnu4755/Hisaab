# CLAUDE.md: Hisaab

High-volume payment reconciliation engine: bank statement CSV against an internal ledger, with every mismatch explained. Part of a six-project portfolio platform that runs on one free Oracle ARM VM.

- **Source of truth:** the PDF `~/Downloads/Portfolio Projects Complete Build & Free Deployment Plan.pdf`.
- **Progress and deviations:** `../PROGRESS.md`. Update it after every step. Any change to the PDF's design goes under "Deviations from the plan" with the reason; never change the design silently.

## Rules (PDF "Coding rules")
1. Class-based views only (`BaseView`, a Flask `MethodView`) with **only `get()` and `post()`**. Updates and deletes are a POST to an action endpoint (for example `POST /claims/<id>/approve`). `tests/test_view_rules.py` enforces it.
2. Layers: **View** (HTTP only) → **Service** (business logic) → **Repository** (DB access) → **Model**. A view never writes SQL or opens sessions; a repository never imports Flask.
3. Errors: raise `AppError` subclasses from `app/exceptions.py`. One global handler returns `{"error": {"code", "message", "details", "request_id"}}`. No bare `except:`, no `print`, no swallowed errors (log with `logger.exception` when a loop must continue).
4. Every request is validated with Pydantic (`BaseView.parse_body` / `parse_query`) before it reaches a service.
5. Config from environment variables only (`app/config.py`, fails fast at boot). `.env.example` is committed, `.env` never is.
6. Structured JSON logs with `request_id` on every line. Allowed extra fields: `code`, `error_message`, `job_id`, `service`.
7. pytest for every service class, against real PostgreSQL (never SQLite). External calls (LLM, Telegram, Docker, HTTP) are mocked. CI fails under 80% coverage.

Also: Python 3.12, type hints everywhere, `ruff check .` clean, small commits prefixed `feat:` `fix:` `test:` `docs:` `chore:`. Push after every verified change (the local folder was once deleted by an outside process; GitHub is the backup).

## Layout (PDF 2.1)
`app/{__init__,config,extensions,exceptions,error_handlers,logging_setup}.py`, `app/models`, `app/schemas`, `app/repositories`, `app/services`, `app/views` (`base.py`, `health.py`, `__init__.py` with `register_routes`), `migrations/` (Alembic), `tests/`, `wsgi.py`, `gunicorn.conf.py`, `Dockerfile`, `ui/` (React UI, PDF 3A).

## Commands
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock -r requirements-dev.txt
# local test databases: docker container "portfolio-test-pg" on port 55432 (password "test")
export TEST_DATABASE_URL=postgresql+psycopg://postgres:test@localhost:55432/hisaab_test
ruff check . && pytest --cov=app --cov-report=term-missing
DATABASE_URL=... alembic revision --autogenerate -m "describe change" && alembic upgrade head
docker build -t hisaab .
```
Dependencies: runtime packages go in `requirements.txt`, test tools in `requirements-dev.txt`. `requirements.lock` is the frozen runtime set (`pip freeze` of a runtime-only install, or of the built image), so pytest never ships in the image.

## Hisaab specifics (PDF 3.3)
- **Flow:** `POST /runs` (multipart `bank_file` + `ledger_file`, `.csv` only, 50 MB cap) checks both headers (422), saves files to `DATA_DIR/runs/<id>/` and creates a run in `PENDING` (202). The worker (`python -m app.worker`) claims runs with `SKIP LOCKED`, reconciles with Polars, stores the summary and every unmatched row in batches of 5,000, then deletes the files.
- **Tables:** `recon_runs` (+ bank_rows, ledger_rows, started_at, finished_at), `recon_exceptions` (run_id, side, txn_date, amount_paise, reference).
- **CSV columns:** bank `txn_date, amount, reference`; ledger `entry_date, amount, reference`; dates `YYYY-MM-DD`. Money is integer paise.
- **Matching:** pass 1 exact (reference + amount, one-to-one via an occurrence index); pass 2 same amount and nearest date within ±2 days. A ledger row never matches twice.
- **Endpoints:** `POST /runs`, `GET /runs`, `GET /runs/<id>`, `GET /runs/<id>/exceptions?side=bank|ledger`, `GET /runs/<id>/exceptions.csv`.
- **Benchmark:** `scripts/generate_data.py` makes 1,000,000 rows; `python -m app.cli create-run` queues files larger than the upload limit; publish time and peak RAM.
- **Tests to write:** exact match; date off by 2 days matches, off by 3 does not; duplicate ledger rows never match twice; missing column returns 422; amounts with commas parse.
- **UI accent:** amber (hue 75).
