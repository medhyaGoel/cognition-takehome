# Build vs. buy: replacing Power Apps with a Devin-built internal-tools platform

**Audience:** VP Engineering, Series C fintech (~60 engineers).

## Recommendation
**Build, in phases. Don't do a big-bang cut-over.** Move the refunds dashboard first onto the
foundation in this repo, run it in parallel with Power Apps for one close cycle, then migrate the
feature-flag admin and the KYC queue. Build the 10+ planned tools on the foundation from the start.
Renew Power Apps for one more term only if citizen developers (non-engineers) currently build or
maintain apps the team depends on. That is the one capability this approach does not replace.

## What the prototype proves (evidence is in the repo)
| Claim | Evidence |
|---|---|
| **Fast:** a correct, secure app plus its foundation in about 1 hour | Repo created 21:30. Skeleton merged 22:01. Refunds core merged 22:15 and workflows/outbox/email merged 22:24, both built by parallel Devin sessions. Frontend and security hardening PRs followed. Timestamps are in the PR history. |
| **Correct money handling** | Integer cents. DB-enforced refund cap. Atomic reservation, idempotency keys with request-hash checks, versioned state transitions, provider call outside the transaction, reconciliation. Concurrency tests on real Postgres. |
| **Power Apps-grade governance** | OIDC (Entra by config), RBAC with a route × role × business-unit test matrix, Postgres RLS (≈ Dataverse business units), DB-enforced maker-checker, append-only audit, PII masking, read-only connector, CI gates (gitleaks, semgrep, pip-audit, npm audit, Dependabot). |
| **Power Automate-style workflows** | Declarative YAML rules: auto-approve small refunds, 2 approvals for large ones, SLA escalation, failure paging. Notifications go through a transactional outbox to in-app, webhook and email (SMTP). |
| **The next app is cheap** | ~8.8K lines of Devin-written code across backend, frontend and tests. App #2 reuses auth, RLS, audit, workflows and UI (see the feature-flag app, if built). |

## Cost comparison (fill in with the client's numbers; assumptions are labelled)
| | Buy (Power Apps, status quo) | Build (this approach) |
|---|---|---|
| License / platform | ~$250K/yr (client figure), grows with seats and premium connectors | Hosting for 1 Postgres + 1 container service + IdP (existing Entra): typically low five figures/yr *(assumption, size to their cloud)* |
| Build cost | Per-app low-code effort by makers | Devin sessions + engineer review. This prototype: ~1 hour wall-clock for the foundation and the first app |
| Maintenance | Microsoft owns the platform; the team owns app logic | The team owns everything: patching, on-call, dependency upgrades, SOC 2 change evidence. **Budget ~0.5–1 FTE** across the portfolio *(assumption)*, much of it delegable to Devin (Dependabot PRs, CVE fixes, test upkeep) |
| Break-even | n/a | If maintenance stays under ~1 FTE plus hosting, build costs less than the license from year 1 *(depends on their loaded FTE cost)* |

## Security: what gets better, and what gets harder
- **Better:** every control is code that is reviewed, versioned and tested. Business-unit isolation and
  maker-checker are enforced by the database even if app code has a bug. Workflow rules are
  reviewed like code, unlike flows edited in a UI. Correctness for money (atomicity, idempotency) is
  not something low-code platforms give you.
- **Harder / now yours:** vulnerability patching, secrets management, production hardening (managed
  Postgres, WAF, backups/PITR, monitoring/alerting), access reviews, and audit evidence for SOC 2 / PCI
  scope. These are documented gaps in the threat model, not solved by the prototype.

## Opportunity cost
- **Against building:** engineers own another platform. The risk is foundation drift as apps
  multiply, which is why `AGENTS.md`, the CI policy matrix (new routes fail CI without a policy) and
  the "new table must have RLS" test exist.
- **Against buying:** $250K/yr plus a ceiling on correctness, and tools that can't be tested or code-reviewed.
  A 60-engineer team's internal tools stay outside its normal engineering quality bar.
- **With Devin as the builder,** the main build cost moves from engineer time to review time. That is
  what makes the build side viable for a team this size.

## What Devin can't replicate (be explicit with the VP)
- **Citizen development:** business users building apps in a visual designer. Here, apps are built by
  engineers (or by Devin and reviewed by engineers). Mitigation: business users request changes in plain
  English, Devin opens a PR, and engineers approve it. This is a process, not a WYSIWYG tool.
- **Microsoft ecosystem glue:** Teams/SharePoint/Excel connectors and 1,000+ prebuilt connectors.
  Each integration here is an adapter someone writes (cheap with Devin, but not free).
- **Vendor-owned compliance and SLA:** Microsoft's certifications cover their platform. Here the team
  produces its own evidence.

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| Foundation erodes as apps are added | Conventions file, CI policy/RLS tests that fail closed, Devin Review on every PR |
| Key-person risk | Boring, widely known stack (FastAPI/Postgres/React), docs, tests as the spec |
| Production gaps | Phased rollout with parallel run. Production checklist from the threat model before cut-over |
| Underestimated maintenance | Track actual hours per app for 2 quarters before decommissioning Power Apps |

## Proposed next steps
1. Production-hardening sprint for refunds: managed Postgres, Entra app registration, secrets manager,
   monitoring, real payment-provider adapter (~1–2 weeks).
2. Run refunds in parallel with Power Apps for one month-end close, then reconcile.
3. Migrate the feature-flag admin, then the KYC queue. Measure the build time for each.
4. Decide on the Power Apps renewal with real maintenance data.
