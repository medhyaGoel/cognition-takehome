import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderWithApp, type Call } from "../../../test/harness";
import type { RefundDetail } from "../types";
import { RefundDetailPage, STALE_MESSAGE } from "./RefundDetailPage";

const ID = "33333333-3333-4333-a333-333333333333";

function detail(over: Partial<RefundDetail> = {}): RefundDetail {
  return {
    id: ID,
    payment_id: "p1",
    business_unit: "US",
    amount_cents: 4200,
    currency: "USD",
    reason: "Duplicate charge",
    status: "pending_approval",
    requested_by: "alice@example.com",
    required_approvals: 1,
    approvals_count: 0,
    provider_refund_id: null,
    failure_reason: null,
    version: 1,
    escalated_at: null,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    approvals: [],
    payment: {
      id: "p1",
      provider_ref: "pi_1001",
      customer_name: "Jane Doe",
      customer_email: "j***@customer.test",
      amount_cents: 10000,
      currency: "USD",
      business_unit: "US",
      reserved_cents: 4200,
      refunded_cents: 0,
      refundable_cents: 5800,
      captured_at: "2026-10-01T00:00:00Z",
    },
    ...over,
  };
}

function render(persona: string, handler: (c: Call) => Response | Promise<Response>) {
  return renderWithApp(<RefundDetailPage />, { persona, handler, path: "/refunds/:id", route: `/refunds/${ID}` });
}

const gets = (calls: Call[]) => calls.filter((c) => c.method === "GET" && c.path === `/api/refunds/${ID}`);

describe("RefundDetailPage", () => {
  it("approves with expected_version; on 409 shows the stale message and refetches", async () => {
    let version = 1;
    const { calls } = render("bob", (c) => {
      if (c.path.endsWith("/audit")) return jsonResponse(200, []);
      if (c.method === "POST") {
        version = 2; // someone else changed it in the meantime
        return jsonResponse(409, { error: { code: "version_conflict", message: "stale" } });
      }
      return jsonResponse(200, detail({ version }));
    });
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "looks fine" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Approve" }).at(-1)!);
    expect(await screen.findByText(STALE_MESSAGE)).toBeTruthy();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`/api/refunds/${ID}/approve`);
    expect(post.body).toEqual({ expected_version: 1, comment: "looks fine" });
    await waitFor(() => expect(gets(calls).length).toBe(2));
    expect(await screen.findByText("2")).toBeTruthy(); // reloaded version shown
  });

  it("hides Approve for the requester, and still surfaces a server 403", async () => {
    render("dave", (c) => {
      if (c.path.endsWith("/audit")) return jsonResponse(200, []);
      if (c.method === "POST") return jsonResponse(403, { error: { code: "forbidden", message: "Requester cannot decide" } });
      return jsonResponse(200, detail({ requested_by: "dave@example.com" }));
    });
    await screen.findByText(/You requested this refund/);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "dup" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Reject" }).at(-1)!);
    expect(await screen.findByText(/Not allowed: Requester cannot decide/)).toBeTruthy();
  });

  it("shows no decision buttons to viewers and renders the masked email as served", async () => {
    render("erin", (c) => (c.path.endsWith("/audit") ? jsonResponse(200, []) : jsonResponse(200, detail())));
    expect(await screen.findByText("j***@customer.test")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
  });

  it("reads 'Not found or outside your business units' on 404", async () => {
    render("bob", () => jsonResponse(404, { error: { code: "not_found", message: "Refund not found" } }));
    expect(await screen.findByText("Not found or outside your business units")).toBeTruthy();
  });

  it("renders the audit timeline", async () => {
    render("bob", (c) =>
      c.path.endsWith("/audit")
        ? jsonResponse(200, [
            {
              id: 1,
              occurred_at: "2026-11-01T00:00:00Z",
              actor: "alice@example.com",
              action: "refund.created",
              entity_type: "refund",
              entity_id: ID,
              before: null,
              after: { status: "pending_approval" },
              request_id: "req-1",
            },
          ])
        : jsonResponse(200, detail()),
    );
    expect(await screen.findByText("refund.created")).toBeTruthy();
    expect(screen.getByText("pending_approval")).toBeTruthy();
  });
});
