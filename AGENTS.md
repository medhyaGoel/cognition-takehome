# Conventions for humans and AI agents (Devin reads this file)

This repo is a **shared foundation for internal tools** (`backend/app/platform/`) plus apps built
on it (`backend/app/apps/<app>/`, `frontend/`). Read `docs/api-contract.md` before changing APIs.

## Non-negotiables (fintech correctness)
1. **Money is integer minor units** (`*_cents`, `bigint`) + ISO currency. Never floats/Decimal-in-JSON.
2. **One transaction per write**, opened in the service layer: `with db.begin(): ...`.
   Audit rows (`platform.audit.record`) and outbox messages (`platform.outbox.enqueue`) are written
   **inside that same transaction**.
3. **Invariants live in the database too** (CHECK / UNIQUE / triggers), not only in Python.
4. **State transitions are conditional updates** (`UPDATE ... WHERE id=:id AND status=:from AND
   version=:v`) and bump `version`. Zero rows updated → 409. No read-modify-write without a lock.
5. **Every mutating endpoint is idempotent** (Idempotency-Key header or naturally idempotent).
6. **No external side effects inside a DB transaction.** Commit an intent state, call the external
   system with an idempotency key, commit the result (see refunds worker).
7. **Authorization is server-side and fail-closed**: every `/api` route (except the documented
   public ones) uses `Depends(require_roles(...))` from `app.platform.auth`. UI hiding is not security.
8. **PII is masked** for roles that don't need it; never log tokens, emails, or payloads with PII.

## Layout & ownership
| path | owns |
|---|---|
| `backend/app/platform/` | auth/RBAC, audit, outbox, workflows engine, connectors, errors |
| `backend/app/apps/<app>/` | `models` (in `app/models.py` for now), `schemas.py`, `service.py`, `router.py` |
| `backend/migrations/versions/` | schema; **one new migration per PR**, never edit a merged one |
| `config/workflows.yaml` | declarative workflow rules (reviewed like code) |
| `frontend/` | React + Vite + TypeScript UI |
| `docs/` | contract, threat model, ADRs |

Routers stay thin (parse → call service → serialize). Business rules live in `service.py` and are
tested directly and through the API.

## Testing
- Tests run against **real Postgres** (`docker compose up -d db`; the suite creates `app_test`).
- Concurrency claims need a concurrency test (threads + separate sessions, assert exact outcomes).
- Use `tests.conftest.auth_headers(email)` for authenticated requests.
- Before pushing: `cd backend && ruff check . && ruff format --check . && pytest`.

## Commands
```bash
docker compose up --build          # db + api (:8000) + worker (+ web once added)
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
pytest                             # needs the compose db running
```
