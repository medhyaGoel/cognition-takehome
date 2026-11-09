# API contract (v1)

Base path `/api`. JSON everywhere. Auth: `Authorization: Bearer <OIDC access token>` issued by
the IdP (Keycloak locally at `http://localhost:8080/realms/internal-tools`; Entra ID/Okta/Google in
production). The SPA uses Authorization Code + PKCE with client `internal-tools-web`.
Money is **integer cents** + ISO currency.
Timestamps are ISO-8601 UTC. IDs are UUID strings.

Errors (all non-2xx from domain logic):
```json
{"error": {"code": "conflict", "message": "human readable"}}
```
| status | code | when |
|---|---|---|
| 401 | — (FastAPI `detail`) | missing/invalid token |
| 403 | `forbidden` or FastAPI `detail` | role missing, or self-approval |
| 404 | `not_found` | unknown id |
| 409 | `conflict` / `version_conflict` / `invalid_transition` / `idempotency_mismatch` / `insufficient_refundable_amount` | concurrency or state violations — client refetches |
| 422 | `validation_failed` or FastAPI validation | bad input |

## Roles and business units
Roles: `viewer`, `refund_agent`, `refund_approver`, `finance_admin`, `flag_editor`,
`flag_approver` (token claim `roles`).
Business units: `US`, `EU` (token claim `groups`: `bu-us`, `bu-eu`). Row-level security limits
every read and write to the caller's business units (Dataverse business-units equivalent):
rows outside them are invisible, so their ids return **404**, not 403.

Demo users (password `demo-password`, local Keycloak only):
| user | roles | business units |
|---|---|---|
| alice | refund_agent, flag_editor | US |
| frank | refund_agent, flag_editor | EU |
| bob | refund_approver | US |
| carol | refund_approver, flag_approver | US, EU |
| dave | finance_admin, refund_approver, flag_approver | US, EU |
| erin | viewer | US |
Subjects are emails (`alice@example.com`).

## Platform (exists)
| method | path | roles | notes |
|---|---|---|---|
| GET | `/healthz` | public | |
| GET | `/api/auth/config` | public | `{issuer, client_id, audience}` for the SPA's OIDC client |
| GET | `/api/me` | any authenticated | `{subject, display_name, roles[], business_units[]}` |

## Refunds
`ALL_READ = viewer, refund_agent, refund_approver, finance_admin`

| method | path | roles | notes |
|---|---|---|---|
| GET | `/api/payments?q=` | ALL_READ | search by provider_ref / customer name; ≤50 rows (RLS-scoped) |
| GET | `/api/payments/{id}` | ALL_READ | Payment + `refundable_cents` |
| GET | `/api/refunds?status=&limit=&offset=` | ALL_READ | newest first, limit ≤100 |
| GET | `/api/refunds/{id}` | ALL_READ | RefundDetail (refund + approvals + payment summary) |
| POST | `/api/refunds` | refund_agent, finance_admin | header `Idempotency-Key` (8–128 chars) **required**; body `{payment_id, amount_cents, reason}` → 201 Refund. Replay with same key+body → 200 same Refund. Same key, different body → 409 `idempotency_mismatch` |
| POST | `/api/refunds/{id}/approve` | refund_approver, finance_admin | body `{expected_version, comment?}`. Requester can't approve own (403). One decision per approver. When approvals ≥ required → `approved` |
| POST | `/api/refunds/{id}/reject` | refund_approver, finance_admin | body `{expected_version, reason}` → `rejected`, releases reservation |
| GET | `/api/refunds/{id}/audit` | ALL_READ | audit entries for the refund, oldest first |
| GET | `/api/refunds/export.csv?status=` | finance_admin | CSV export (Excel connector equivalent) |

### Schemas
```text
Payment { id, provider_ref, customer_name, customer_email*, amount_cents, currency,
          business_unit, reserved_cents, refunded_cents, refundable_cents, captured_at }
          *masked ("a***@customer.test") for callers without refund_agent/refund_approver/finance_admin
Refund  { id, payment_id, business_unit, amount_cents, currency, reason, status, requested_by,
          required_approvals, approvals_count, provider_refund_id, failure_reason,
          version, escalated_at, created_at, updated_at }
RefundDetail = Refund + { approvals: [{approver, decision, comment, created_at}],
                          payment: Payment }
AuditEntry { id, occurred_at, actor, action, entity_type, entity_id, before, after, request_id }
```

### Refund state machine
```
pending_approval --approve (enough approvals)--> approved --worker--> processing --> succeeded
        |                                                                 \--> failed
        \--reject--> rejected
```
- Creation **reserves** the amount on the payment atomically
  (`reserved_cents + amount <= amount_cents`, also a DB CHECK). `rejected`/`failed` release it;
  `succeeded` moves it into `refunded_cents`.
- Every transition is a conditional update on `(id, status, version)`; `version` increments.
  A stale `expected_version` → 409 `version_conflict`.
- `required_approvals` is set at creation by the workflow engine (default 1; 0 = auto-approve).
  Auto-approval is audited with the requester as actor, action `refund.auto_approved`, and the
  matched rule ids in `after.rules`.
- `business_unit` is copied from the payment (composite FKs keep payment, refund, and approvals
  in the same business unit). Audit entries carry it too.
- Execution against the payment provider happens in the worker, never in a request:
  `approved → processing` (commit) → provider call with idempotency key = refund id →
  `succeeded|failed` (commit). Stuck `processing` rows are reconciled by re-calling the
  (idempotent) provider.

## Feature flags
`FLAG_READ` = every role. One `flag_states` row per flag × business unit × environment
(`dev`, `staging`, `prod`), RLS-scoped by business unit like refunds (out-of-BU ids → 404).

| method | path | roles | notes |
|---|---|---|---|
| GET | `/api/flags` | FLAG_READ | flat list of flag states in the caller's BUs: `{id, flag_key, description, business_unit, environment, enabled, version, updated_at, updated_by}` |
| POST | `/api/flags/states/{id}/toggle` | flag_editor | **Idempotency-Key required**. Body `{enabled, expected_version}`. dev/staging: applied now (`status=applied`). prod: creates a change request (`status=pending`). 201 = created, 200 = replay. 409 `version_conflict` (stale), `no_change`, `idempotency_mismatch` |
| GET | `/api/flags/states/{id}/audit` | FLAG_READ | audit timeline of one flag state (`flag.toggled`, `flag.change_requested`, `flag.change_approved`, `flag.change_rejected`) |
| GET | `/api/flags/change-requests?status=` | FLAG_READ | newest first, max 200: `{id, flag_state_id, flag_key, business_unit, environment, desired_enabled, expected_version, status, requested_by, decided_by, decided_at, decision_reason, version, created_at}` |
| POST | `/api/flags/change-requests/{id}/approve` | flag_approver | body `{expected_version, reason?}`. Maker-checker: requester → 403 `self_approval` (also enforced by DB trigger). Applies the change atomically, conditional on the flag version the requester saw; 409 if the flag moved or the request was already decided |
| POST | `/api/flags/change-requests/{id}/reject` | flag_approver | body `{expected_version, reason?}`; same checks; the flag is unchanged |

Domain events: `flag.toggled`, `flag.change_requested`, `flag.change_approved`,
`flag.change_rejected` (outbox topic + `workflows.on_event`, `entity_type=flag_change_request`);
`data`: `flag_key, business_unit, environment, desired_enabled, state, status, change_request_id`.
Rule `flag-prod-change-needs-approval` notifies `flag_approver` in-app and by email.

## Workflows / notifications
| method | path | roles | notes |
|---|---|---|---|
| GET | `/api/workflows/rules` | ALL_READ | the loaded rules (read-only; edited via PR to `config/workflows.yaml`) |
| GET | `/api/notifications?limit=` | finance_admin, refund_approver | in-app notifications (table `notifications`, RLS-scoped), newest first: `{id, created_at, topic, title, body, recipient_role, entity_type, entity_id, business_unit}` |

Domain events emitted by refunds (`app.platform.workflows.on_event`): `refund.created`,
`refund.approved`, `refund.rejected`, `refund.succeeded`, `refund.failed`.
`data` always includes `amount_cents`, `currency`, `status`, `requested_by`, `payment_id`,
`business_unit`.
