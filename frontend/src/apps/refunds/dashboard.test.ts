import { describe, expect, it } from "vitest";
import { computeDashboard, median } from "./dashboard";
import type { Refund } from "./types";

const NOW = Date.parse("2026-11-08T12:00:00Z");
const H = 3600_000;
const D = 24 * H;
let n = 0;
function refund(over: Partial<Refund>): Refund {
  n += 1;
  const created = over.created_at ?? new Date(NOW - D).toISOString();
  return {
    id: `r${n}`,
    payment_id: "p",
    business_unit: "US",
    amount_cents: 1000,
    currency: "USD",
    reason: "x",
    status: "pending_approval",
    requested_by: "alice@example.com",
    required_approvals: 1,
    approvals_count: 0,
    provider_refund_id: null,
    failure_reason: null,
    version: 1,
    escalated_at: null,
    created_at: created,
    updated_at: created,
    ...over,
  };
}

describe("computeDashboard", () => {
  const rows = [
    refund({ amount_cents: 1001 }),
    refund({ amount_cents: 2002, requested_by: "bob@example.com" }),
    refund({ amount_cents: 999, business_unit: "EU", currency: "EUR" }),
    refund({ status: "approved", amount_cents: 500, created_at: new Date(NOW - 10 * H).toISOString(), updated_at: new Date(NOW - 8 * H).toISOString() }),
    refund({ status: "approved", amount_cents: 700, created_at: new Date(NOW - 10 * H).toISOString(), updated_at: new Date(NOW - 6 * H).toISOString() }),
    refund({ status: "approved", required_approvals: 0, amount_cents: 100 }),
    refund({ status: "processing", amount_cents: 300 }),
    refund({ status: "succeeded", amount_cents: 4000, updated_at: new Date(NOW - 2 * D).toISOString() }),
    refund({ status: "succeeded", amount_cents: 9999, updated_at: new Date(NOW - 8 * D).toISOString(), created_at: new Date(NOW - 9 * D).toISOString() }),
    refund({ status: "failed", amount_cents: 1013 }),
  ];

  it("computes KPIs in integer cents per currency", () => {
    const s = computeDashboard(rows, { businessUnit: null, subject: "bob@example.com", canApprove: true, now: NOW });
    expect(s.pending).toEqual({ count: 3, totals: { USD: 3003, EUR: 999 } });
    expect(s.awaitingPayout).toEqual({ count: 4, totals: { USD: 1600 } });
    expect(s.refunded7d).toEqual({ count: 1, totals: { USD: 4000 } });
    expect(s.failed).toEqual({ count: 1, totals: { USD: 1013 } });
    // required>0 and approved or later: 2h, 4h (approved), 0 (processing), 0 (failed), 1d (succeeded);
    // auto-approved, pending and negative (bad fixture) durations excluded -> median 2h.
    expect(s.timeToApproveSample).toBe(5);
    expect(s.medianTimeToApproveMs).toBe(2 * H);
    expect(s.timeToApproveExact).toBe(false);
  });

  it("time-to-approve is exact when every sample is still awaiting payout", () => {
    const s = computeDashboard(rows.slice(0, 6), { businessUnit: null, subject: "x", canApprove: false, now: NOW });
    expect(s.timeToApproveSample).toBe(2);
    expect(s.medianTimeToApproveMs).toBe(3 * H);
    expect(s.timeToApproveExact).toBe(true);
  });

  it("needs-my-attention excludes the user's own requests and non-pending refunds", () => {
    const s = computeDashboard(rows, { businessUnit: null, subject: "BOB@example.com", canApprove: true, now: NOW });
    expect(s.attention.map((r) => r.amount_cents).sort()).toEqual([1001, 999].sort());
    const viewer = computeDashboard(rows, { businessUnit: null, subject: "erin@example.com", canApprove: false, now: NOW });
    expect(viewer.attention).toEqual([]);
  });

  it("filters by business unit", () => {
    const s = computeDashboard(rows, { businessUnit: "EU", subject: "bob@example.com", canApprove: true, now: NOW });
    expect(s.pending).toEqual({ count: 1, totals: { EUR: 999 } });
    expect(s.failed.count).toBe(0);
  });

  it("buckets the last 14 days by created date", () => {
    const s = computeDashboard(rows, { businessUnit: null, subject: "x", canApprove: false, now: NOW });
    expect(s.byDay).toHaveLength(14);
    expect(s.byDay.reduce((a, d) => a + d.total, 0)).toBe(rows.length);
  });

  it("median handles even, odd and empty", () => {
    expect(median([])).toBeNull();
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 2, 3])).toBe(2.5);
  });
});
