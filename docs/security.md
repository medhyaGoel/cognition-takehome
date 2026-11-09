# Security: Power Apps primitives, rebuilt as code

Each Power Apps / Dataverse built-in has an equivalent here that is enforced server-side (most in
the database) and proven by a test that runs in CI against real Postgres as the real runtime roles.

| Power Apps primitive | Our equivalent | Where enforced | Test that proves it |
|---|---|---|---|
| **Entra ID sign-in** | OIDC; API verifies RS256 access tokens (iss, aud, exp, kid via JWKS, verified email); Keycloak locally, Entra in prod via `OIDC_*` env | `backend/app/platform/auth.py`, `keycloak/internal-tools-realm.json` | `tests/security/test_access_matrix.py` (forged/expired/alg-confusion/unknown-kid -> 401; JWKS outage -> 503), `tests/security/test_realm.py` |
| **Security roles** | `require_roles(...)` on every route; POLICY table transcribed from the contract; new routes fail CI until they declare a policy | route dependencies; `tests/security/test_access_matrix.py::POLICY` | `test_every_api_route_requires_authentication`, `test_every_api_route_declares_a_policy`, `test_role_matrix` (route x persona) |
| **Business units** | Postgres row-level security on `business_unit`, context from the token's `groups` set per transaction | `migrations/versions/0002_governance.py`, `backend/app/db.py` | `tests/security/test_access_matrix.py::test_business_unit_matrix` (route x persona x BU: 2xx in own BU, 404 + no state change outside), `::test_list_routes_return_only_callers_business_units`, `tests/test_governance.py::test_rls_*`, `tests/security/test_db_governance.py::test_every_business_unit_table_has_rls_and_an_app_api_policy`, `::test_rls_context_does_not_leak_to_next_checkout` |
| **Field-level security** | PII masking by role (`mask_email`, `mask_name`); column-level UPDATE grants | `backend/app/platform/masking.py`, `0002` | `tests/security/test_masking.py`, `tests/test_governance.py::test_runtime_role_cannot_delete_or_touch_immutable_columns` |
| **Auditing** | Append-only `audit_log` written in the same transaction; actor must equal the authenticated user; trigger blocks UPDATE/DELETE for everyone | `0001`, `0002`, `backend/app/platform/audit.py` | `tests/test_governance.py::test_audit_log_is_append_only_and_unforgeable`, `::test_audit_log_append_only_even_for_owner`, `tests/security/test_db_governance.py::test_audit_log_is_insert_only_for_runtime_roles` |
| **Approvals** | Maker-checker in the DB (no self-approval trigger, approver pinned to caller by RLS); approvals routes limited to approver roles | `0001`, `0002`, `require_roles` | `tests/test_governance.py::test_maker_checker_enforced_by_database`, `test_role_matrix` for `/approve` and `/reject` |
| **Connectors / DLP** | Connector interface with read-only credentials to the system of record; no DELETE/TRUNCATE/DDL for runtime roles; secrets scanning and SAST in CI | `backend/app/platform/connectors/`, `0002`, `.github/workflows/security.yml` | `tests/security/test_db_governance.py::test_runtime_roles_have_no_delete_truncate_or_ddl_privileges`, gitleaks + semgrep jobs |
| **Environments** | `APP_ENV=production` refuses dev defaults, disables OpenAPI docs, adds HSTS; CORS off unless exact origins are configured | `backend/app/config.py`, `backend/app/platform/security.py`, `backend/app/main.py` | `tests/security/test_hardening.py::test_production_app_does_not_serve_docs`, `::test_cors_*` |

Also enforced on every response (no Power Apps equivalent needed, since the platform hosts it):
CSP `default-src 'none'`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `no-store` on `/api`,
64 KiB body limit, generic 500s, and log redaction of tokens and emails (`test_hardening.py`).

## Production-readiness checklist
- [ ] Entra ID app registration: app roles -> `roles`, BU security groups -> `groups`; MFA +
      Conditional Access + Smart Lockout; delete the dev password-grant client.
- [ ] `APP_ENV=production` with every `OIDC_*` / `*_DATABASE_URL` set from the secret manager.
- [ ] DB roles provisioned by the platform team with `docs/sql/production-roles.sql`; migrations
      run from CI as the migrator; runtime credentials rotated.
- [ ] Private networking: DB not public; egress allowlist (IdP JWKS, payment provider).
- [ ] TLS at the edge and to the DB; HSTS on.
- [ ] Edge rate limiting / WAF.
- [ ] `CORS_ALLOWED_ORIGINS` left unset unless the SPA is cross-origin (exact https origins only).
- [ ] Audit log + IdP events shipped to the SIEM, with alerting.
- [ ] Backups with point-in-time recovery and a tested restore.
- [ ] Branch protection: CI and Security workflows required; Dependabot PRs reviewed.
- [ ] Run CI with `STRICT_POLICY=1` once every contract route is implemented.
- [ ] Threat model (`docs/threat-model.md`) reviewed for each new app on the platform.
