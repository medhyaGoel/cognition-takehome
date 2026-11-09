# internal-tools-platform

A shared foundation for internal tools plus apps built on it (refunds dashboard first,
feature-flag admin next), built with Devin to evaluate replacing Microsoft Power Apps. The focus
is fintech-grade correctness and Power Apps' built-in governance, rebuilt as code the team owns.

> Work in progress: this is the platform skeleton. App features land in follow-up PRs.

## Power Apps primitive → equivalent here
| Power Apps / Dataverse | This repo |
|---|---|
| Entra ID sign-in | OIDC (Keycloak locally; Entra/Okta/Google via `OIDC_*` env) — `backend/app/platform/auth.py` |
| Security roles | Server-side RBAC on every route (`require_roles`) |
| Business units / record-level access | Postgres row-level security by business unit — `migrations/versions/0002_governance.py` |
| Field-level security | PII masking by role — `platform/masking.py`; column-level DB grants |
| Auditing | Append-only audit log in the same transaction as the change (DB trigger + grants) |
| Approvals | Maker-checker enforced in the database (trigger + RLS on approver identity) |
| Connectors + DLP | Connector interface with read-only credentials — `platform/connectors/` |
| Power Automate | Declarative workflow rules + transactional outbox — `config/workflows.yaml` |
| Environments | `APP_ENV=production` refuses dev defaults |

## Architecture (local)
```
browser ──OIDC (PKCE)──▶ keycloak :8080
   │ bearer token
   ▼
 api :8000 (FastAPI, role app_api, RLS) ──▶ db (app Postgres: refunds, approvals, audit, outbox)
 worker   (role app_worker)              ──▶ db
 migrate  (schema owner, one-shot)       ──▶ db + source-db (read-only: client's system of record)
```

## Run
```bash
docker compose up --build
curl localhost:8000/healthz
# a token for scripts (dev-only client):
curl -s localhost:8080/realms/internal-tools/protocol/openid-connect/token \
  -d grant_type=password -d client_id=internal-tools-dev-cli -d username=alice -d password=demo-password
```
Demo data: the source DB seeds ~300 synthetic charges. For refund history on the dashboard, run
`docker compose exec api python -m app.demo_seed` (creates 40 refunds through the real API as the demo
users, approves/rejects/pays them out, then backdates them over 14 days; safe to re-run).

Demo users and roles: see `docs/api-contract.md`. Keycloak admin console: http://localhost:8080 (admin/admin, local only).

## Test
```bash
docker compose up -d db
cd backend && pip install -r requirements-dev.txt && ruff check . && ruff format --check . && pytest
```
Tests run against real Postgres as the real runtime roles, so RLS and grants are exercised.

Conventions for humans and agents: `AGENTS.md`. API contract: `docs/api-contract.md`.
