import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { fakeFetch, jsonResponse, PERSONAS } from "../../test/harness";
import { AuthProvider, useAuth } from "./AuthProvider";
import { Can } from "./Can";
import type { AuthBackend, Me } from "./types";

const UNAUTHORIZED = { error: { code: "unauthorized", message: "Token rejected" } };

/** A backend whose token changes on renewal, like oidc-client-ts after signinSilent. */
function fakeBackend(renewOk = true) {
  let token = "old";
  const listeners = new Set<() => void>();
  const backend: AuthBackend = {
    kind: "test",
    init: async () => "signed_in",
    getAccessToken: async () => token,
    onUnauthorized: vi.fn(async () => {
      if (renewOk) token = "new";
      return renewOk;
    }),
    switchUser: async () => {},
    signOut: async () => {},
    subscribe: (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
  const silentRenew = (next: string) => {
    token = next;
    for (const l of listeners) l();
  };
  return { backend, silentRenew };
}

function Probe() {
  const { me } = useAuth();
  return (
    <div>
      <span data-testid="who">{me.subject}</span>
      <Can roles={["finance_admin"]}>
        <button type="button">Export CSV</button>
      </Can>
    </div>
  );
}

function renderAuth(backend: AuthBackend, meFor: (token: string) => Me | null) {
  const { fetch, calls } = fakeFetch((call) => {
    const me = meFor((call.headers.authorization ?? "").replace("Bearer ", ""));
    return me ? jsonResponse(200, me) : jsonResponse(401, UNAUTHORIZED);
  });
  render(
    <AuthProvider
      backend={backend}
      transport={fetch}
      fallback={(p) => <div data-testid="fallback">{typeof p === "string" ? p : `error: ${p.error}`}</div>}
    >
      <Probe />
    </AuthProvider>,
  );
  return calls;
}

describe("AuthProvider", () => {
  it("a 401 on /api/me followed by a successful renewal ends authenticated", async () => {
    const { backend } = fakeBackend(true);
    const calls = renderAuth(backend, (t) => (t === "new" ? PERSONAS.alice! : null));
    expect((await screen.findByTestId("who")).textContent).toBe("alice@example.com");
    expect(screen.queryByTestId("fallback")).toBeNull();
    expect(backend.onUnauthorized).toHaveBeenCalledTimes(1);
    expect(calls.map((c) => c.headers.authorization)).toEqual(["Bearer old", "Bearer new"]);
  });

  it("enters the error phase only when renewal fails", async () => {
    const { backend } = fakeBackend(false);
    renderAuth(backend, () => null);
    expect((await screen.findByTestId("fallback")).textContent).toMatch(/^error: .*could not be renewed/);
  });

  it("re-reads /api/me after a silent renewal so new roles show up", async () => {
    const { backend, silentRenew } = fakeBackend();
    const promoted: Me = { ...PERSONAS.alice!, roles: ["refund_agent", "finance_admin"] };
    renderAuth(backend, (t) => (t === "promoted" ? promoted : PERSONAS.alice!));
    await screen.findByTestId("who");
    expect(screen.queryByRole("button", { name: "Export CSV" })).toBeNull();
    await act(async () => silentRenew("promoted"));
    expect(await screen.findByRole("button", { name: "Export CSV" })).toBeTruthy();
  });
});
