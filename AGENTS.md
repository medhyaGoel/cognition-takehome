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
9. **Requests run as the least-privilege `app_api` DB role under row-level security.** Use the
   `get_db` dependency (it binds the session to the caller; RLS scopes rows to their business
   units). Background jobs use `WorkerSessionLocal` (`app_worker` role, actor `system:worker`).
   Never serve traffic with the schema-owner connection.
10. **Audit actor = the authenticated user** (the DB rejects anything else for `app_api`).
    Automated decisions taken inside a user's request are recorded with that user as actor and
    the rule in the action/after payload (e.g. `refund.auto_approved`, `after.rules=[...]`).
11. **Every new table** ships, in its migration, with: `business_unit` (if it holds business
    data), grants for `app_api`/`app_worker` (no DELETE; column-level UPDATE), RLS enabled, and
    policies. See `migrations/versions/0002_governance.py` for the pattern.

## Layout & ownership
| path | owns |
|---|---|
| `backend/app/platform/` | auth (OIDC)/RBAC, audit, outbox, workflows engine, connectors, errors |
| `backend/app/platform/connectors/` | adapters to external systems (system of record, payment provider) |
| `backend/app/bootstrap.py` | dev bootstrap: migrate, enable runtime DB logins, sync payments |
| `keycloak/` | local OIDC IdP realm (users, roles, business-unit groups) |
| `source-db/` | stand-in for the client's payments system of record (read-only to us) |
| `backend/app/apps/<app>/` | `models` (in `app/models.py` for now), `schemas.py`, `service.py`, `router.py` |
| `backend/migrations/versions/` | schema; only the refunds agent may add one (`0003_*`); never edit a merged one |
| `config/workflows.yaml` | declarative workflow rules (reviewed like code) |
| `frontend/` | React + Vite + TypeScript UI |
| `docs/` | contract, threat model, ADRs |

Routers stay thin (parse → call service → serialize). Business rules live in `service.py` and are
tested directly and through the API.

## Testing
- Tests run against **real Postgres** as the **real runtime roles** (`docker compose up -d db`;
  the suite recreates `app_test` + `source_test` and runs migrations). RLS and grants apply.
- Concurrency claims need a concurrency test (threads + separate sessions, assert exact outcomes).
- Identities: `tests.helpers.auth_headers("alice")` (personas in `tests/helpers.py`, mirroring
  the Keycloak realm). Tokens are RS256, shaped like real Keycloak tokens.
- Fixtures (`tests/conftest.py`): `client`, `db_as("bob")` (app_api session as a persona),
  `worker_db`, `make_payment(amount_cents=, business_unit=, currency=)`, `owner_engine`
  (setup only).
- Before pushing: `cd backend && ruff check . && ruff format --check . && pytest`.

## Commands
```bash
docker compose up --build          # db, source-db, keycloak (:8080), migrate, api (:8000), worker (+ web)
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
# token for scripts (dev-only client; the SPA uses auth code + PKCE):
curl -s localhost:8080/realms/internal-tools/protocol/openid-connect/token \
  -d grant_type=password -d client_id=internal-tools-dev-cli -d username=alice -d password=demo-password
pytest                             # needs the compose db running
```
