# System overview: a secure internal-tools foundation + a refunds app (built with Devin)

## In plain English
Power Apps gives this team three things: apps get built fast, every app inherits the same
security and governance, and apps can trigger automated workflows (approvals, notifications).
This repo rebuilds those as **ordinary code the team owns**, with the refunds dashboard
on top as the first app. Because it's code, it also gets what Power Apps lacks: version control,
code review, automated tests, and real transactional correctness for money.

The shared **foundation** is built once (sign-in, roles, business-unit data access, audit trail,
workflow engine, connectors, UI kit, CI security gates). Each new tool adds only its own screens
and rules, and inherits the same guarantees.

## Power Apps security primitives, rebuilt
| Power Apps / Dataverse | Here | Enforced by |
|---|---|---|
| Entra ID sign-in | Standard OIDC login (Keycloak locally; Entra ID in production is config only). The app never sees passwords. | Identity provider + API token checks |
| Security roles | Agent / approver / finance admin / viewer, checked on every endpoint | API + a test covering every endpoint × role |
| Business units | US and EU staff only see their own region's records | Postgres row-level security |
| Field-level security | Customer emails masked for roles that don't need them; app can't edit payment amounts | API masking + column-level DB permissions |
| Auditing | Every change logs who / what / before / after; can't be edited, deleted, or forged | DB trigger + permissions + RLS |
| Approvals | Requester can't approve own refund; large refunds need 2 approvers | DB trigger + workflow rules |
| Connectors + DLP | Client's payments DB is read through one adapter with read-only credentials | Connector interface + DB grants |
| Environments | Production refuses dev settings | Startup config checks |

## Where the data lives
- **Client's system of record** (stand-in: a separate database). We only read it, through a
  connector. Swapping in their real Postgres, Mongo, or Stripe means writing one adapter.
- **Our app database**: refunds, approvals, audit log, notifications. The app connects as a
  restricted user (no deletes, row-level security on), never as the database owner.
- A transaction can't span two databases, so our database reserves refund amounts atomically,
  the payment provider is the final check (idempotent calls), and mismatches between the two
  systems are flagged for reconciliation rather than overwritten.

## Money-correctness guarantees (each has an automated test)
- **No over-refunds**, even with many simultaneous clicks: the database enforces refunded ≤ paid.
- **Double-clicks and retries are safe**: repeats return the original result.
- **Two approvers clicking at once** produce exactly one decision.
- **Payouts never run inside a database transaction**: record "processing", call the provider
  with a unique key, record the result. A recovery job finishes interrupted payouts without paying twice.

## Who built what
Contract-first: the skeleton fixed the schema, API contract and conventions, then four Devin
sessions built in parallel, each owning its own directories and opening its own PR.

| Session | Built | PR |
|---|---|---|
| Coordinator | Schema, OIDC login, DB roles + RLS, audit/outbox, connector to the client DB, API contract, conventions, CI; synthetic data + demo seed; review follow-ups | [#1](https://github.com/medhyaGoel/internal-tools-platform/pull/1), [#4](https://github.com/medhyaGoel/internal-tools-platform/pull/4), [#6](https://github.com/medhyaGoel/internal-tools-platform/pull/6), [#8](https://github.com/medhyaGoel/internal-tools-platform/pull/8) |
| A: Refunds core | Refund endpoints, approvals, payout worker, mock payment provider, concurrency/idempotency/business-unit tests | [#2](https://github.com/medhyaGoel/internal-tools-platform/pull/2) |
| B: Workflows | Rules engine + `config/workflows.yaml` (auto-approve small, 2 approvers for large, SLA escalation, failure alerts), outbox delivery (in-app, webhook, email via SMTP/Mailpit), worker loop | [#5](https://github.com/medhyaGoel/internal-tools-platform/pull/5) |
| C: Frontend | React app: SSO login, shared UI kit, dashboard, refund queue / detail / approve, notifications, rules page | [#7](https://github.com/medhyaGoel/internal-tools-platform/pull/7) |
| D: Security | Route × role × business-unit matrix, token attack tests, DB-permission tests, security headers, CI scans, threat model | [#3](https://github.com/medhyaGoel/internal-tools-platform/pull/3) |

Every PR went through CI and Devin Review; valid findings were fixed before or right after merge.

## Deliberately out of scope
Cloud deployment, real Entra/Stripe/Teams credentials, a visual workflow designer. The
foundation has the seams for each; the writeup lists them as production steps.
