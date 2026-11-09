// Dev-only (VITE_USE_MOCKS=1): stands in for the workflow/notification endpoints that aren't on
// main yet. Everything else (OIDC, /api/me, payments, refunds) passes through to the real API.
// Not a security boundary: it calls the real API with the caller's own token for data and roles.
import type { FetchLike } from "../platform/api/client";
import type { Refund } from "../apps/refunds/types";
import { centsToDecimalString } from "../platform/money";

export const MOCK_RULES = {
  rules: [
    {
      id: "auto-approve-small",
      description: "Refunds under 20.00 are auto-approved.",
      when: { event: "refund.created", amount_cents: { lt: 2000 } },
      then: { required_approvals: 0 },
    },
    {
      id: "dual-approval-large",
      description: "Refunds of 500.00 or more need two approvers.",
      when: { event: "refund.created", amount_cents: { gte: 50000 } },
      then: { required_approvals: 2, notify: ["refund_approver"] },
    },
    {
      id: "notify-finance-on-failure",
      description: "Tell finance when the provider declines a refund.",
      when: { event: "refund.failed" },
      then: { notify: ["finance_admin"] },
    },
  ],
};

const ALL_READ = ["viewer", "refund_agent", "refund_approver", "finance_admin"];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Mock": "1" },
  });
}

export function withWorkflowMocks(real: FetchLike): FetchLike {
  return async (input, init) => {
    const url = new URL(input, window.location.origin);
    const isRules = url.pathname === "/api/workflows/rules";
    const isNotifs = url.pathname === "/api/notifications";
    if (!(isRules || isNotifs) || (init?.method ?? "GET") !== "GET") return real(input, init);

    const auth = new Headers(init?.headers).get("Authorization");
    const headers = { Accept: "application/json", ...(auth ? { Authorization: auth } : {}) };
    const meRes = await real("/api/me", { headers });
    if (!meRes.ok) return meRes;
    const me = (await meRes.json()) as { roles: string[] };
    const allowed = isRules ? ALL_READ : ["refund_approver", "finance_admin"];
    if (!me.roles.some((r) => allowed.includes(r))) return json(403, { detail: "Insufficient role" });
    if (isRules) return json(200, MOCK_RULES);

    const limit = Math.min(100, Number(url.searchParams.get("limit") ?? 50) || 50);
    const res = await real(`/api/refunds?limit=${limit}`, { headers });
    if (!res.ok) return res;
    const refunds = (await res.json()) as Refund[];
    const isFinance = me.roles.includes("finance_admin");
    const notifications = refunds
      .filter((r) => r.status === "pending_approval" || (isFinance && r.status === "failed"))
      .map((r) => {
        const failed = r.status === "failed";
        return {
          id: `mock-${r.id}`,
          created_at: failed ? r.updated_at : r.created_at,
          topic: failed ? "refund.failed" : "refund.created",
          title: failed ? "Refund declined by provider" : "Refund awaiting approval",
          body: `${r.requested_by} requested ${centsToDecimalString(r.amount_cents)} ${r.currency}: ${r.reason}`,
          recipient_role: failed ? "finance_admin" : "refund_approver",
          entity_type: "refund",
          entity_id: r.id,
          business_unit: r.business_unit,
        };
      })
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    return json(200, notifications);
  };
}
