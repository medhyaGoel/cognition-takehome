# Threat model: refunds flow (STRIDE)

Scope: an agent requests a refund on a payment, an approver approves or rejects it, the worker
executes it against the payment provider. Prototype status: mitigations marked **here** are in
this repo and tested; **prod** items are what a production deployment must add.

## Assets
| asset | why it matters |
|---|---|
| Money movement (refund execution) | direct financial loss / fraud |
| Payment ledger (`reserved_cents`, `refunded_cents`) | over-refund if inconsistent |
| Customer PII (name, email) | privacy / GDPR |
| Audit log | non-repudiation, SOX-style evidence |
| Access tokens, DB credentials, provider API keys | full compromise of the above |

## Trust boundaries
```
[Browser SPA] --(1) OIDC PKCE--> [IdP: Keycloak / Entra ID]
     | (2) bearer access token (RS256)
     v
[API (FastAPI, role app_api)] --(3) SQL under RLS--> [App DB (Postgres)] <--(4)-- [Worker (app_worker)]
                                                          ^                         |
     [Client system of record (read-only)] --(5) sync ----+          (6) idempotent call
                                                                                    v
                                                                     [Payment provider]
```
1. Browser <-> IdP: users authenticate only at the IdP; the API never sees passwords.
2. Browser -> API: untrusted input; every request carries a bearer token verified by the API.
3. API -> DB: the API connects as `app_api`; the DB enforces business-unit RLS, column grants,
   append-only audit, and maker-checker even if API code is wrong.
4. Worker -> DB: `app_worker`, cross-BU by explicit policy, still no DELETE / audit UPDATE.
5. System of record -> DB: read-only credentials; only the schema owner writes the mirror.
6. Worker -> provider: outside any DB transaction, idempotency key = refund id.

## STRIDE
| # | threat | mitigation (here) | where / proof |
|---|---|---|---|
| S1 | Forged or replayed token; alg=none / HS256-with-public-key confusion; token from another IdP or for another API | RS256 only, header checked before any key lookup; sub/iss/aud/exp/iat required; JWKS by `kid`; verified email required; typ must be an access token; 8 KiB cap | `backend/app/platform/auth.py`; `tests/security/test_access_matrix.py::test_bad_tokens_get_401_on_every_protected_route` |
| S2 | Password guessing at login | IdP brute-force lockout (5 failures, incremental wait), password policy, no self-registration | `keycloak/internal-tools-realm.json`; `tests/security/test_realm.py` (verified live: correct password rejected after 5 failures) |
| S3 | Impersonating another user in audit/approvals | Subject comes only from the verified token; DB RLS `WITH CHECK actor/approver/requested_by = app_user()` | `migrations/versions/0002_governance.py`; `tests/test_governance.py` |
| T1 | Tampering with amounts / immutable columns | Column-level UPDATE grants only; CHECK `0 <= refunded <= reserved <= amount`; money as integer cents | `0001_initial.py`, `0002_governance.py`; `test_governance.py::test_runtime_role_cannot_delete_or_touch_immutable_columns` |
| T2 | Cross-business-unit writes | RLS `USING`/`WITH CHECK` on business unit; composite FKs keep refund/payment/approval in one BU | `0002`; `test_governance.py::test_cannot_write_into_other_business_unit` |
| T3 | Body-size / parser abuse | 64 KiB body limit (413), unframed bodies rejected (411), streamed bytes counted | `backend/app/platform/security.py`; `tests/security/test_hardening.py` |
| R1 | User denies having approved | Append-only audit in the same transaction as the change, actor = authenticated user; trigger blocks UPDATE/DELETE even for the owner | `0001`/`0002`; `test_governance.py::test_audit_log_*` |
| I1 | Reading or acting on another BU's refunds/payments | RLS: rows outside the caller's BUs are invisible (404, not 403, no side effect); fail-closed without context | `0002`, `backend/app/db.py`; `test_governance.py::test_rls_*`, `test_access_matrix.py::test_business_unit_matrix`, `::test_list_routes_return_only_callers_business_units` |
| I2 | Identity leaking between pooled connections | RLS context set with transaction-local `set_config(..., true)` | `backend/app/db.py`; `tests/security/test_db_governance.py::test_rls_context_does_not_leak_to_next_checkout`, `::test_concurrent_sessions_see_only_their_business_units` |
| I3 | PII exposure to viewers | Email masked for roles without PII access | `backend/app/platform/masking.py`; `tests/security/test_masking.py` |
| I4 | Leaks via errors / logs | Generic 500 body; 401/503 without details; log filter redacts bearer tokens, JWTs, emails (incl. tracebacks) | `security.py`, `main.py`; `test_hardening.py::test_unhandled_errors_are_generic_500`, `::test_redacting_filter_*` |
| I5 | Browser-side leakage (caching, framing, MIME sniffing, cross-origin reads) | CSP `default-src 'none'`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `Cache-Control: no-store` on /api; CORS off unless exact origins configured; no cookies | `security.py`; `test_hardening.py` |
| I6 | API surface discovery | OpenAPI/Swagger not served when `APP_ENV=production` | `security.fastapi_settings`; `test_hardening.py::test_production_app_does_not_serve_docs` |
| D1 | IdP outage / JWKS flood with random `kid`s | Generic 503, never 500; PyJWT JWKS cache + refresh cooldown; bad algs rejected before any fetch | `auth.py`; `test_access_matrix.py::test_jwks_*`, `::test_bad_algorithm_never_reaches_key_resolver` |
| D2 | Double-submit / retries creating duplicate refunds or payouts | Idempotency-Key on create (UNIQUE `(requested_by, idempotency_key)`); provider idempotency key = refund id | contract + `0001`; refunds tests (refunds agent) |
| E1 | Calling an endpoint without the right role; new endpoint shipped without auth | `require_roles` on every route; CI enumerates every served route (including included routers and include-level dependencies) and fails on any /api route without `get_principal`, without a POLICY entry, or without declared business-unit scoping; full route x persona x business-unit matrix on real rows | `tests/security/test_access_matrix.py` |
| E2 | Self-approval (maker-checker bypass) | DB trigger rejects approver = requester; RLS pins approver to caller | `0001`; `test_governance.py::test_maker_checker_enforced_by_database` |
| E3 | Runtime role escalation (BYPASSRLS, DDL, DELETE, owning tables, SECURITY DEFINER) | Least-privilege roles; catalog checks fail CI if a migration regresses them or adds a BU table without RLS | `0002`; `tests/security/test_db_governance.py` |
| E4 | Vulnerable dependency / leaked secret / insecure code pattern | gitleaks (full history), semgrep (python, OWASP, secrets), pip-audit, npm audit; SHA-pinned actions; Dependabot with 7-day cooldown | `.github/workflows/security.yml`, `.github/dependabot.yml`, `.gitleaks.toml` |

## Residual risk / what production needs
- **IdP**: Microsoft Entra ID instead of Keycloak (set `OIDC_ISSUER`/`OIDC_AUDIENCE`/`OIDC_JWKS_URL`);
  MFA + Conditional Access + Smart Lockout; app roles and groups mapped to `roles`/`groups`
  claims; remove the dev password-grant client. Entra access tokens carry `preferred_username`
  rather than `email_verified`; adapt the subject claim when switching.
- **Secrets**: DB and provider credentials from a managed secret store (Key Vault / Secrets
  Manager), rotated; prefer Entra/IAM DB auth. Nothing in env files.
- **DB roles provisioned externally** (`docs/sql/production-roles.sql`); migrations run from CI
  as the migrator role only; runtime roles never own objects.
- **SQL injection is the main RLS escape**: a runtime role can call `set_config('app.business_units', ...)`
  itself, so injection would defeat BU isolation. Mitigated by parameterised SQL only (ruff `S608`,
  semgrep); production could move context into a signed/verified mechanism or per-BU roles.
- **Network isolation**: API and worker in a private network; DB not publicly reachable; egress
  allowlist (IdP JWKS, provider API); the system of record over private link.
- **TLS everywhere** (HSTS is sent when `APP_ENV=production`; terminate TLS at the edge, TLS to DB).
- **Rate limiting / WAF** at the edge (not implemented here).
- **Backups / PITR** for the app DB, tested restores; audit log retention policy.
- **SIEM**: ship the audit log (e.g. outbox -> Sentinel/Splunk) and IdP sign-in/admin events;
  alert on denied-access spikes, 401 bursts, and approvals outside business hours.
- **Frontend**: when served, a CSP for the SPA itself (this repo's CSP covers API responses).
