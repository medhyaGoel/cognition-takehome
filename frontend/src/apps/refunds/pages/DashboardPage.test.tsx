import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderWithApp } from "../../../test/harness";
import type { Refund, RefundDetail } from "../types";
import { DashboardPage } from "./DashboardPage";

const H = 3600_000;
const base = Date.now() - 3 * 24 * H;
const iso = (h: number) => new Date(base + h * H).toISOString();

function refund(id: string, status: Refund["status"], over: Partial<Refund> = {}): Refund {
  return {
    id,
    payment_id: "p",
    business_unit: "US",
    amount_cents: 1000,
    currency: "USD",
    reason: "x",
    status,
    requested_by: "alice@example.com",
    required_approvals: 1,
    approvals_count: 1,
    provider_refund_id: null,
    failure_reason: null,
    version: 3,
    escalated_at: null,
    created_at: iso(0),
    // Paid out long after approval: updated_at must not be used as the decision time.
    updated_at: iso(40),
    ...over,
  };
}

const rows = [
  refund("r-succeeded", "succeeded"),
  refund("r-failed", "failed"),
  refund("r-processing", "processing"),
  refund("r-auto", "succeeded", { required_approvals: 0, approvals_count: 0 }),
  refund("r-pending", "pending_approval", { approvals_count: 0 }),
];
const approvedAfterHours: Record<string, number> = { "r-succeeded": 2, "r-failed": 4, "r-processing": 6 };

describe("DashboardPage", () => {
  it("median time to approve uses approval decision timestamps across succeeded, failed and processing", async () => {
    const { calls } = renderWithApp(<DashboardPage />, {
      handler: (c) => {
        if (c.path.startsWith("/api/refunds?")) return jsonResponse(200, rows);
        const id = c.path.replace("/api/refunds/", "");
        const r = rows.find((x) => x.id === id);
        if (!r) return jsonResponse(404, { error: { code: "not_found", message: "nope" } });
        const detail: RefundDetail = {
          ...r,
          approvals: [{ approver: "bob@example.com", decision: "approved", comment: null, created_at: iso(approvedAfterHours[id]!) }],
          payment: {} as RefundDetail["payment"],
        };
        return jsonResponse(200, detail);
      },
    });
    const card = (await screen.findByText("Median time to approve")).closest(".kpi") as HTMLElement;
    await waitFor(() => expect(card.textContent).toContain("4.0h"));
    expect(card.textContent).toContain("3 approved refunds");
    expect(card.textContent).not.toContain("awaiting payout");
    const detailGets = calls.filter((c) => /^\/api\/refunds\/r-/.test(c.path)).map((c) => c.path).sort();
    expect(detailGets).toEqual(["/api/refunds/r-failed", "/api/refunds/r-processing", "/api/refunds/r-succeeded"]);
  });
});
