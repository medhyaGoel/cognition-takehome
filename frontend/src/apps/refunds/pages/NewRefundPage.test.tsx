import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderWithApp, type Call } from "../../../test/harness";
import type { Payment, Refund } from "../types";
import { NewRefundPage } from "./NewRefundPage";

const payment: Payment = {
  id: "11111111-1111-4111-a111-111111111111",
  provider_ref: "ch_1001",
  customer_name: "Jane Doe",
  customer_email: "jane@customer.test",
  amount_cents: 10000,
  currency: "USD",
  business_unit: "US",
  reserved_cents: 2500,
  refunded_cents: 0,
  refundable_cents: 7500,
  captured_at: "2026-10-01T00:00:00Z",
};

const refund = { id: "22222222-2222-4222-a222-222222222222" } as Refund;

function keyFactory() {
  let n = 0;
  return () => `key-${++n}-xxxxxxxx`;
}

async function fillForm(amount: string) {
  fireEvent.change(screen.getByPlaceholderText(/ch_2003/), { target: { value: "ch_1" } });
  fireEvent.click(await screen.findByRole("button", { name: /ch_1001/ }));
  await screen.findByTestId("refundable");
  fireEvent.change(screen.getByPlaceholderText("0.00"), { target: { value: amount } });
  fireEvent.change(screen.getByRole("textbox", { name: /reason/i }), { target: { value: "Duplicate charge" } });
}

function baseHandler(onCreate: (c: Call) => Response | Promise<Response>) {
  return (c: Call) => {
    if (c.path === "/api/payments" || c.path.startsWith("/api/payments?")) return jsonResponse(200, [payment]);
    if (c.path === `/api/payments/${payment.id}`) return jsonResponse(200, payment);
    if (c.path === "/api/refunds" && c.method === "POST") return onCreate(c);
    return jsonResponse(404, { error: { code: "not_found", message: "?" } });
  };
}

const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");

describe("NewRefundPage", () => {
  it("lists payments before anything is typed, with refundable amounts, and disables exhausted ones", async () => {
    const exhausted: Payment = {
      ...payment,
      id: "33333333-3333-4333-a333-333333333333",
      provider_ref: "ch_2003",
      customer_name: "Ada Nakamura",
      business_unit: "US",
      reserved_cents: 0,
      refunded_cents: 10000,
      refundable_cents: 0,
    };
    const { calls } = renderWithApp(<NewRefundPage newKey={keyFactory()} />, {
      persona: "alice",
      handler: (c) => (c.path === "/api/payments" ? jsonResponse(200, [payment, exhausted]) : jsonResponse(404, {})),
    });
    const open = await screen.findByRole("button", { name: /Jane Doe/ });
    expect(open.textContent).toContain("ch_1001");
    expect(open.textContent).toContain("US");
    expect(open.textContent).toContain("$100.00");
    expect(open.textContent).toContain("$75.00 refundable");
    expect((open as HTMLButtonElement).disabled).toBe(false);
    const done = screen.getByRole("button", { name: /Ada Nakamura/ }) as HTMLButtonElement;
    expect(done.disabled).toBe(true);
    expect(done.textContent).toContain("Nothing refundable");
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["GET /api/payments"]);
    expect(screen.getByPlaceholderText("ch_2003 or Ada Nakamura")).toBeTruthy();
  });

  it("suggests searching by customer name or charge reference when nothing matches", async () => {
    renderWithApp(<NewRefundPage newKey={keyFactory()} />, {
      persona: "alice",
      handler: (c) => (c.path.startsWith("/api/payments") ? jsonResponse(200, []) : jsonResponse(404, {})),
    });
    fireEvent.change(screen.getByPlaceholderText(/ch_2003/), { target: { value: "Medhya" } });
    expect(await screen.findByText('No payments match "Medhya"')).toBeTruthy();
    expect(screen.getByText(/Search by customer name .* or charge reference/)).toBeTruthy();
  });

  it("sends integer cents parsed from the string input and shows the refundable amount", async () => {
    const { calls } = renderWithApp(<NewRefundPage newKey={keyFactory()} />, {
      persona: "alice",
      handler: baseHandler(() => jsonResponse(201, refund)),
    });
    await fillForm("0.29");
    expect(screen.getByTestId("refundable").textContent).toBe("$75.00");
    fireEvent.click(screen.getByRole("button", { name: "Request refund" }));
    await screen.findByTestId("navigated");
    expect(posts(calls)[0]!.body).toEqual({ payment_id: payment.id, amount_cents: 29, reason: "Duplicate charge" });
  });

  it("reuses the same Idempotency-Key when retrying after a network error", async () => {
    let attempt = 0;
    const { calls } = renderWithApp(<NewRefundPage newKey={keyFactory()} />, {
      persona: "alice",
      handler: baseHandler(() => {
        attempt += 1;
        if (attempt === 1) throw new TypeError("Failed to fetch");
        return jsonResponse(200, refund); // server replays the refund created by attempt 1
      }),
    });
    await fillForm("10.13");
    fireEvent.click(screen.getByRole("button", { name: "Request refund" }));
    await screen.findByText(/reuses the same idempotency key/);
    fireEvent.click(screen.getByRole("button", { name: "Retry submission" }));
    await screen.findByTestId("navigated");
    const keys = posts(calls).map((c) => c.headers["idempotency-key"]);
    expect(keys).toEqual(["key-1-xxxxxxxx", "key-1-xxxxxxxx"]);
  });

  it("uses a fresh key after a definitive server answer", async () => {
    let attempt = 0;
    const { calls } = renderWithApp(<NewRefundPage newKey={keyFactory()} />, {
      persona: "alice",
      handler: baseHandler(() => {
        attempt += 1;
        if (attempt === 1) return jsonResponse(422, { detail: [{ msg: "bad reason" }] });
        return jsonResponse(201, refund);
      }),
    });
    await fillForm("5");
    fireEvent.click(screen.getByRole("button", { name: "Request refund" }));
    await screen.findByText("bad reason");
    fireEvent.click(screen.getByRole("button", { name: "Request refund" }));
    await screen.findByTestId("navigated");
    expect(posts(calls).map((c) => c.headers["idempotency-key"])).toEqual(["key-1-xxxxxxxx", "key-2-xxxxxxxx"]);
  });

  it("drops double submits while a request is in flight", async () => {
    let release!: (r: Response) => void;
    const { calls } = renderWithApp(<NewRefundPage newKey={keyFactory()} />, {
      persona: "alice",
      handler: baseHandler(() => new Promise<Response>((res) => (release = res))),
    });
    await fillForm("5");
    const btn = screen.getByRole("button", { name: "Request refund" });
    const form = btn.closest("form")!;
    act(() => {
      fireEvent.submit(form);
      fireEvent.submit(form);
      fireEvent.click(btn);
    });
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(screen.getByRole("button", { name: "Submitting…" })).toHaveProperty("disabled", true);
    await act(async () => release(jsonResponse(201, refund)));
    await screen.findByTestId("navigated");
    expect(posts(calls)).toHaveLength(1);
  });

  it("blocks amounts over the refundable balance and malformed input client-side", async () => {
    const { calls } = renderWithApp(<NewRefundPage newKey={keyFactory()} />, {
      persona: "alice",
      handler: baseHandler(() => jsonResponse(201, refund)),
    });
    await fillForm("75.01");
    fireEvent.click(screen.getByRole("button", { name: "Request refund" }));
    expect(await screen.findByText(/Exceeds refundable amount/)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("0.00"), { target: { value: "1.234" } });
    expect(await screen.findByText("At most 2 decimal places")).toBeTruthy();
    expect(posts(calls)).toHaveLength(0);
  });
});
