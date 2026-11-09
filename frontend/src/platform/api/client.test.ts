import { describe, expect, it, vi } from "vitest";
import { jsonResponse } from "../../test/harness";
import { ApiClient, asList } from "./client";
import { ApiError } from "./errors";

function client(res: () => Response | Promise<Response>, onUnauthorized = vi.fn()) {
  const fetch = vi.fn(async (_input: string, _init?: RequestInit) => res());
  return { api: new ApiClient({ getAccessToken: async () => "tok", fetch, onUnauthorized }), fetch, onUnauthorized };
}

async function failure(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    return e as ApiError;
  }
  throw new Error("expected rejection");
}

describe("ApiClient", () => {
  it("sends the bearer token and JSON body, and builds the query string", async () => {
    const { api, fetch } = client(() => jsonResponse(201, { id: "r1" }));
    const res = await api.post("/api/refunds", { a: 1 }, { headers: { "Idempotency-Key": "k-12345678" } });
    expect(res).toEqual({ status: 201, data: { id: "r1" } });
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("/api/refunds");
    const h = init!.headers as Record<string, string>;
    expect(h.Authorization).toBe("Bearer tok");
    expect(h["Idempotency-Key"]).toBe("k-12345678");
    expect(h["Content-Type"]).toBe("application/json");
    expect(init!.body).toBe('{"a":1}');
    expect(init!.credentials).toBe("omit");

    await api.get("/api/refunds", { query: { status: "approved", limit: 10, offset: undefined } });
    expect(fetch.mock.calls[1]![0]).toBe("/api/refunds?status=approved&limit=10");
  });

  it("maps the contract error envelope", async () => {
    const { api } = client(() => jsonResponse(409, { error: { code: "version_conflict", message: "stale" } }));
    const e = await failure(api.get("/x"));
    expect([e.status, e.kind, e.code, e.message]).toEqual([409, "conflict", "version_conflict", "stale"]);
    expect(e.outcomeUnknown).toBe(false);
  });

  it("maps FastAPI detail errors and distinguishes 401 / 403", async () => {
    const on401 = vi.fn();
    const unauth = client(() => jsonResponse(401, { detail: "Invalid token" }), on401);
    const e401 = await failure(unauth.api.get("/x"));
    expect([e401.kind, e401.message]).toEqual(["unauthorized", "Invalid token"]);
    expect(on401).toHaveBeenCalledOnce();

    const on403 = vi.fn();
    const forbidden = client(() => jsonResponse(403, { detail: "Insufficient role" }), on403);
    const e403 = await failure(forbidden.api.get("/x"));
    expect([e403.kind, e403.code]).toEqual(["forbidden", "forbidden"]);
    expect(on403).not.toHaveBeenCalled();

    const v = client(() => jsonResponse(422, { detail: [{ msg: "field required" }, { msg: "too long" }] }));
    const e422 = await failure(v.api.get("/x"));
    expect([e422.kind, e422.message]).toEqual(["validation", "field required; too long"]);
  });

  it("maps 404 and non-JSON 5xx", async () => {
    const nf = client(() => jsonResponse(404, { error: { code: "not_found", message: "Refund not found" } }));
    expect((await failure(nf.api.get("/x"))).kind).toBe("not_found");
    const s = client(() => new Response("<html>bad gateway</html>", { status: 502 }));
    const e = await failure(s.api.get("/x"));
    expect([e.kind, e.outcomeUnknown]).toEqual(["server", true]);
  });

  it("turns fetch rejections into network errors with unknown outcome", async () => {
    const { api } = client(() => Promise.reject(new TypeError("Failed to fetch")));
    const e = await failure(api.get("/x"));
    expect([e.status, e.kind, e.outcomeUnknown]).toEqual([0, "network", true]);
  });

  it("asList accepts bare arrays and envelopes", () => {
    expect(asList([1])).toEqual([1]);
    expect(asList({ items: [2] })).toEqual([2]);
    expect(asList({ rules: [3] })).toEqual([3]);
    expect(asList(null)).toEqual([]);
  });
});
