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
  id: string;
  created_at: string;
  topic: string;
  title: string;
  body: string;
  recipient_role: string;
  entity_type: string;
  entity_id: string;
  business_unit: string;
}

export interface CreateRefundBody {
  payment_id: string;
  amount_cents: number;
  reason: string;
}
