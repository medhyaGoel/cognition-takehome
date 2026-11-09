import { asList, type ApiClient } from "../../platform/api/client";
import type { AuditEntry } from "../../platform/components/AuditTimeline";
import type {
  CreateRefundBody,
  Notification,
  Payment,
  Refund,
  RefundDetail,
  RefundStatus,
} from "./types";

export const MAX_PAGE = 100;

export const refundsApi = (api: ApiClient) => ({
  searchPayments: async (q: string, signal?: AbortSignal) =>
    asList<Payment>(await api.get("/api/payments", { query: { q }, signal })),

  getPayment: (id: string, signal?: AbortSignal) =>
    api.get<Payment>(`/api/payments/${encodeURIComponent(id)}`, { signal }),

  listRefunds: async (
    params: { status?: RefundStatus | ""; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) => asList<Refund>(await api.get("/api/refunds", { query: params, signal })),

  getRefund: (id: string, signal?: AbortSignal) =>
    api.get<RefundDetail>(`/api/refunds/${encodeURIComponent(id)}`, { signal }),

  getAudit: async (id: string, signal?: AbortSignal) =>
    asList<AuditEntry>(await api.get(`/api/refunds/${encodeURIComponent(id)}/audit`, { signal })),

  /** 201 = created, 200 = idempotent replay of the same key + body. */
  createRefund: (body: CreateRefundBody, idempotencyKey: string) =>
    api.post<Refund>("/api/refunds", body, { headers: { "Idempotency-Key": idempotencyKey } }),

  approve: (id: string, expected_version: number, comment: string) =>
    api.post<Refund>(`/api/refunds/${encodeURIComponent(id)}/approve`, {
      expected_version,
      ...(comment.trim() ? { comment: comment.trim() } : {}),
    }),

  reject: (id: string, expected_version: number, reason: string) =>
    api.post<Refund>(`/api/refunds/${encodeURIComponent(id)}/reject`, {
      expected_version,
      reason: reason.trim(),
    }),

  exportCsv: (status?: RefundStatus | "") =>
    api.raw("/api/refunds/export.csv", {
      query: { status },
      headers: { Accept: "text/csv" },
    }),

  notifications: async (limit = 50, signal?: AbortSignal) =>
    asList<Notification>(await api.get("/api/notifications", { query: { limit }, signal })),

  workflowRules: (signal?: AbortSignal) => api.get<unknown>("/api/workflows/rules", { signal }),
});

/**
 * Pages through GET /api/refunds (max 100 per page) up to `maxRows`, newest first.
 * `truncated` tells the caller the window is incomplete so it can label it.
 */
export async function fetchRecentRefunds(
  api: ApiClient,
  maxRows: number,
  signal?: AbortSignal,
): Promise<{ rows: Refund[]; truncated: boolean }> {
  const r = refundsApi(api);
  const rows: Refund[] = [];
  const seen = new Set<string>();
  let offset = 0;
  while (offset < maxRows) {
    const limit = Math.min(MAX_PAGE, maxRows - offset);
    const page = await r.listRefunds({ limit, offset }, signal);
    offset += page.length;
    for (const row of page) {
      if (!seen.has(row.id)) {
        seen.add(row.id);
        rows.push(row);
      }
    }
    if (page.length < limit) return { rows, truncated: false };
  }
  return { rows, truncated: true };
}
