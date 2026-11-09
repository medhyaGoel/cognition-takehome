// Mirrors docs/api-contract.md (v1). Money is integer cents + ISO currency.

export type RefundStatus =
  | "pending_approval"
  | "approved"
  | "processing"
  | "succeeded"
  | "failed"
  | "rejected";

export const REFUND_STATUSES: RefundStatus[] = [
  "pending_approval",
  "approved",
  "processing",
  "succeeded",
  "failed",
  "rejected",
];

export interface Payment {
  id: string;
  provider_ref: string;
  customer_name: string;
  customer_email: string;
  amount_cents: number;
  currency: string;
  business_unit: string;
  reserved_cents: number;
  refunded_cents: number;
  refundable_cents: number;
  captured_at: string;
}

export interface Refund {
  id: string;
  payment_id: string;
  business_unit: string;
  amount_cents: number;
  currency: string;
  reason: string;
  status: RefundStatus;
  requested_by: string;
  required_approvals: number;
  approvals_count: number;
  provider_refund_id: string | null;
  failure_reason: string | null;
  version: number;
  escalated_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Approval {
  approver: string;
  decision: "approved" | "rejected" | string;
  comment: string | null;
  created_at: string;
}

export interface RefundDetail extends Refund {
  approvals: Approval[];
  payment: Payment;
}

export interface Notification {
  id: number;
  created_at: string;
  topic: string;
  title: string;
  body: string;
  recipient_role: string | null;
  entity_type: string | null;
  entity_id: string | null;
  business_unit: string | null;
}

export interface RuleCondition {
  field: string;
  op: string;
  value: unknown;
}

export type RuleAction =
  | { type: "set_required_approvals"; count: number }
  | { type: "auto_approve" }
  | { type: "notify"; channel: string; adapters: string[]; title: string | null; template: string };

export interface WorkflowRule {
  id: string;
  kind: "event" | "schedule";
  description: string;
  on: string | null;
  conditions: RuleCondition[];
  actions: RuleAction[];
  schedule?: { job: string; every_seconds: number; params: Record<string, unknown> } | null;
}

export interface CreateRefundBody {
  payment_id: string;
  amount_cents: number;
  reason: string;
}
