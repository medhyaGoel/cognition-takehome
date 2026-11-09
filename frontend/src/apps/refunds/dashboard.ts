import type { Refund, RefundStatus } from "./types";

export type CurrencyTotals = Record<string, number>;

export interface Kpi {
  count: number;
  totals: CurrencyTotals;
}

export interface DashboardStats {
  pending: Kpi;
  awaitingPayout: Kpi;
  refunded7d: Kpi;
  failed: Kpi;
  medianTimeToApproveMs: number | null;
  timeToApproveSample: number;
  byDay: DayBucket[];
  attention: Refund[];
}

export type ChartGroup = "pending" | "in_flight" | "succeeded" | "closed";

export const CHART_GROUPS: { key: ChartGroup; label: string; statuses: RefundStatus[] }[] = [
  { key: "pending", label: "Pending approval", statuses: ["pending_approval"] },
  { key: "in_flight", label: "Approved / processing", statuses: ["approved", "processing"] },
  { key: "succeeded", label: "Succeeded", statuses: ["succeeded"] },
  { key: "closed", label: "Rejected / failed", statuses: ["rejected", "failed"] },
];

export interface DayBucket {
  day: string; // YYYY-MM-DD (local)
  total: number;
  counts: Record<ChartGroup, number>;
}

const DAY_MS = 86_400_000;

function kpi(rows: Refund[]): Kpi {
  const totals: CurrencyTotals = {};
  for (const r of rows) {
    if (!Number.isSafeInteger(r.amount_cents)) continue;
    totals[r.currency] = (totals[r.currency] ?? 0) + r.amount_cents;
  }
  return { count: rows.length, totals };
}

function localDay(ms: number): string {
  const d = new Date(ms);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

/**
 * All dashboard numbers, computed client-side from the (RLS-scoped) refund list.
 * `businessUnit` narrows further for the US/EU chip; null = all of the caller's units.
 */
export function computeDashboard(
  all: Refund[],
  opts: {
    businessUnit: string | null;
    subject: string;
    canApprove: boolean;
    now?: number;
    days?: number;
  },
): DashboardStats {
  const now = opts.now ?? Date.now();
  const days = opts.days ?? 14;
  const rows = opts.businessUnit ? all.filter((r) => r.business_unit === opts.businessUnit) : all;
  const by = (...s: RefundStatus[]) => rows.filter((r) => s.includes(r.status));

  // A refund still in `approved` has updated_at == the moment it was approved (exact).
  // Later states overwrite updated_at, so they are excluded; auto-approved (0 required) too.
  const approveDurations = rows
    .filter((r) => r.status === "approved" && r.required_approvals > 0)
    .map((r) => Date.parse(r.updated_at) - Date.parse(r.created_at))
    .filter((ms) => Number.isFinite(ms) && ms >= 0);

  const sevenDaysAgo = now - 7 * DAY_MS;
  const refunded7d = rows.filter((r) => r.status === "succeeded" && Date.parse(r.updated_at) >= sevenDaysAgo);

  const buckets = new Map<string, DayBucket>();
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(start);
    d.setDate(start.getDate() - i);
    const key = localDay(d.getTime());
    buckets.set(key, { day: key, total: 0, counts: { pending: 0, in_flight: 0, succeeded: 0, closed: 0 } });
  }
  for (const r of rows) {
    const b = buckets.get(localDay(Date.parse(r.created_at)));
    if (!b) continue;
    const g = CHART_GROUPS.find((x) => x.statuses.includes(r.status));
    if (!g) continue;
    b.counts[g.key] += 1;
    b.total += 1;
  }

  const me = opts.subject.toLowerCase();
  const attention = opts.canApprove
    ? rows
        .filter((r) => r.status === "pending_approval" && r.requested_by.toLowerCase() !== me)
        .sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
    : [];

  return {
    pending: kpi(by("pending_approval")),
    awaitingPayout: kpi(by("approved", "processing")),
    refunded7d: kpi(refunded7d),
    failed: kpi(by("failed")),
    medianTimeToApproveMs: median(approveDurations),
    timeToApproveSample: approveDurations.length,
    byDay: [...buckets.values()],
    attention,
  };
}
