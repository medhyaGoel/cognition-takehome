import { asList, type ApiClient } from "../../platform/api/client";
import type { AuditEntry } from "../../platform/components/AuditTimeline";

export type Environment = "dev" | "staging" | "prod";
export const ENVIRONMENTS: Environment[] = ["dev", "staging", "prod"];

export interface FlagState {
  id: string;
  flag_key: string;
  description: string;
  business_unit: string;
  environment: Environment;
  enabled: boolean;
  version: number;
  updated_at: string;
  updated_by: string | null;
}

export interface ChangeRequest {
  id: string;
  flag_state_id: string;
  flag_key: string;
  business_unit: string;
  environment: Environment;
  desired_enabled: boolean;
  expected_version: number;
  status: "applied" | "pending" | "approved" | "rejected";
  requested_by: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  version: number;
  created_at: string;
}

export const flagsApi = (api: ApiClient) => ({
  listStates: async (signal?: AbortSignal) =>
    asList<FlagState>(await api.get("/api/flags", { signal })),

  pending: async (signal?: AbortSignal) =>
    asList<ChangeRequest>(
      await api.get("/api/flags/change-requests", {
        query: { status: "pending" },
        signal,
      }),
    ),

  audit: async (stateId: string, signal?: AbortSignal) =>
    asList<AuditEntry>(
      await api.get(`/api/flags/states/${encodeURIComponent(stateId)}/audit`, {
        signal,
      }),
    ),

  /** dev/staging apply at once ("applied"); prod returns a "pending" change request. */
  toggle: (state: FlagState, idempotencyKey: string) =>
    api.post<ChangeRequest>(
      `/api/flags/states/${encodeURIComponent(state.id)}/toggle`,
      { enabled: !state.enabled, expected_version: state.version },
      { headers: { "Idempotency-Key": idempotencyKey } },
    ),

  decide: (
    cr: ChangeRequest,
    decision: "approve" | "reject",
    reason?: string,
  ) =>
    api.post<ChangeRequest>(
      `/api/flags/change-requests/${encodeURIComponent(cr.id)}/${decision}`,
      {
        expected_version: cr.version,
        ...(reason?.trim() ? { reason: reason.trim() } : {}),
      },
    ),
});

export interface FlagRow {
  key: string;
  description: string;
  cells: Record<string, FlagState>;
}

export const cellKey = (bu: string, env: Environment) => `${bu}:${env}`;

/** Pivot flat states into one row per flag with a cell per BU x environment. */
export function toRows(states: FlagState[]): {
  rows: FlagRow[];
  businessUnits: string[];
} {
  const byKey = new Map<string, FlagRow>();
  const bus = new Set<string>();
  for (const s of states) {
    bus.add(s.business_unit);
    const row = byKey.get(s.flag_key) ?? {
      key: s.flag_key,
      description: s.description,
      cells: {},
    };
    row.cells[cellKey(s.business_unit, s.environment)] = s;
    byKey.set(s.flag_key, row);
  }
  return {
    rows: [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key)),
    businessUnits: [...bus].sort((a, b) =>
      a === "US" ? -1 : b === "US" ? 1 : a.localeCompare(b),
    ),
  };
}
