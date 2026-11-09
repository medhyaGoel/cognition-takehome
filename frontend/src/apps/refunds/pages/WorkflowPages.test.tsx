import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderWithApp } from "../../../test/harness";
import type { Notification, WorkflowRule } from "../types";
import { NotificationsPage } from "./NotificationsPage";
import { WorkflowRulesPage } from "./WorkflowRulesPage";

// Shapes mirror backend/app/platform/workflows/router.py (RuleOut, NotificationOut).
const RULES: WorkflowRule[] = [
  {
    id: "refund-large-two-approvals",
    kind: "event",
    description: "Refunds above 50000 minor units need two approvers.",
    on: "refund.created",
    conditions: [{ field: "amount_cents", op: "gt", value: 50000 }],
    actions: [
      { type: "set_required_approvals", count: 2 },
      { type: "notify", channel: "finance-approvals", adapters: ["in_app", "webhook"], title: "T", template: "Body" },
    ],
    schedule: null,
  },
  {
    id: "pending-sla",
    kind: "schedule",
    description: "Escalate stale approvals.",
    on: null,
    conditions: [],
    actions: [{ type: "auto_approve" }],
    schedule: { job: "refunds.escalate_pending", every_seconds: 300, params: { older_than_hours: 24 } },
  },
];

describe("WorkflowRulesPage", () => {
  it("renders event and schedule rules from GET /api/workflows/rules, read-only", async () => {
    const { calls } = renderWithApp(<WorkflowRulesPage />, {
      handler: () => jsonResponse(200, RULES),
    });
    expect(await screen.findByText("refund-large-two-approvals")).toBeTruthy();
    expect(screen.getByText("amount_cents > 50000")).toBeTruthy();
    expect(screen.getByText("Require 2 approvals")).toBeTruthy();
    expect(screen.getByText("#finance-approvals")).toBeTruthy();
    expect(screen.getByText("refunds.escalate_pending")).toBeTruthy();
    expect(screen.getByText(/change through a pull request/)).toBeTruthy();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["GET /api/workflows/rules"]);
    expect(screen.queryByRole("button", { name: /edit|save|delete/i })).toBeNull();
  });
});

describe("NotificationsPage", () => {
  it("renders notifications with nullable fields and links refund entities", async () => {
    const items: Notification[] = [
      {
        id: 2,
        created_at: "2026-11-08T10:00:00Z",
        topic: "refund.failed",
        title: "Refund $10.13 · US",
        body: "Provider declined",
        recipient_role: "finance_admin",
        entity_type: "refund",
        entity_id: "abc",
        business_unit: "US",
      },
      {
        id: 1,
        created_at: "2026-11-08T09:00:00Z",
        topic: "system",
        title: "Digest",
        body: "Nothing to see",
        recipient_role: null,
        entity_type: null,
        entity_id: null,
        business_unit: null,
      },
    ];
    const { calls } = renderWithApp(<NotificationsPage />, { handler: () => jsonResponse(200, items) });
    expect(await screen.findByText("Refund $10.13 · US")).toBeTruthy();
    expect(screen.getByText("Digest")).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /open refund/i })).toHaveLength(1);
    expect(calls[0]?.path).toBe("/api/notifications?limit=50");
  });
});
