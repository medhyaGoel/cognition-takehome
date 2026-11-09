# Key decisions

**Framing.** The VP's real question isn't "can Devin build a refunds app". It's "can we own a
platform for 13+ apps without each one being a new security and correctness risk?" So I built a
**shared foundation plus one deeply correct app**, and kept the second app as a stretch goal
whose job is to show that adding an app is cheap. I didn't try to replicate Power Apps' low-code
designer. For a 60-engineer team, Devin plus a shared foundation *is* the app generator.

**Scope: depth over breadth.** I picked refunds because it's where a fintech VP will probe hardest
(money, concurrency, approvals). I put the feature-flag admin ahead of KYC as app #2 because it
reuses approvals, audit, and notifications without bringing in a second hard domain (documents,
PII workflows). A visual app builder, a cloud deploy, and real Teams/Stripe integrations are out
of scope; each could eat the whole time box.

**Correctness lives in the database, not just the code.**
- Money is integer cents. Over-refunding is blocked by a CHECK constraint, and refund amounts are
  reserved with one conditional UPDATE. Tests use N parallel requests and assert exact outcomes.
- Idempotency keys plus a request hash make retries safe; reusing a key with a different body is
  rejected.
- State transitions use conditional updates and a version column, so concurrent approvers
  produce exactly one transition.
- The payout is never inside a DB transaction: commit `processing`, call the provider with the
  refund id as the idempotency key, then commit the result. A reconciler handles crashes.
- Audit and outbox rows are written in the same transaction as the change, so a rollback can't
  send a notification.
- Tests run on real Postgres, because SQLite serializes writes and would make concurrency tests
  pass vacuously.

**No cross-database atomicity claim.** The client's payments data lives in their own system of
record. We read it through a connector with a read-only role, mirror it, and flag drift instead of
overwriting. Refund reservations are authoritative in our database, and the provider is the final
check. Claiming a two-database atomic transaction would have been the dishonest shortcut.

**Security: rebuild Power Apps' built-in governance primitives one-for-one.**
| Power Apps primitive | Our equivalent |
|---|---|
| Entra ID sign-in | OIDC (Keycloak locally, Entra by config) |
| Security roles | RBAC plus a route × role test |
| Business units | Postgres row-level security, plus DB-enforced maker-checker |
| Auditing | Append-only audit log |
| Connectors / DLP | Read-only connector credentials |

The app runs as a NOBYPASSRLS role with no DELETE and column-level UPDATE. Defense in depth means
an app bug still can't leak another business unit's data or forge an audit entry. I chose
Keycloak over Google/Supabase: it needs no customer credentials, gives a real login page and
RS256/JWKS, and it's the same integration shape as Entra. Supabase couples auth to its own hosted
Postgres, which repeats the lock-in being escaped.

**Speed via parallel Devin sessions and contract-first design.** I wrote the schema, API contract,
conventions (`AGENTS.md`), auth/RLS, and CI first. Then 4 Devin sessions built refunds, workflows,
frontend, and security in parallel, each owning its own directories and opening its own PR,
reviewed by Devin Review before merge. That ordering is the replicable process the client would
use for apps 4–13.

**Stack.** FastAPI, SQLAlchemy, Alembic, Postgres, and React/Vite/TS under Docker Compose: boring,
easy to review, and widely hireable. Docker Compose over a cloud deploy, so the time goes into
correctness rather than infrastructure.

**Biggest risks I'd flag honestly.** Ongoing ownership (on-call, patching, SOC 2 change
management) moves to the client. Power Apps' citizen-developer story doesn't carry over: only
engineers build apps. The value depends on keeping the foundation disciplined as apps multiply.
