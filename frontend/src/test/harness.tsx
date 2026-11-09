import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { vi } from "vitest";
import { ApiClient, type FetchLike } from "../platform/api/client";
import { ApiContext } from "../platform/api/context";
import { AuthContext, type AuthContextValue } from "../platform/auth/AuthProvider";
import type { AuthBackend, Me, Role } from "../platform/auth/types";
import { ToastProvider } from "../platform/components/Toast";

export const PERSONAS: Record<string, Me> = {
  alice: { subject: "alice@example.com", display_name: "Alice", roles: ["refund_agent"], business_units: ["US"] },
  bob: { subject: "bob@example.com", display_name: "Bob", roles: ["refund_approver"], business_units: ["US"] },
  dave: {
    subject: "dave@example.com",
    display_name: "Dave",
    roles: ["finance_admin", "refund_approver"],
    business_units: ["EU", "US"],
  },
  erin: { subject: "erin@example.com", display_name: "Erin", roles: ["viewer"], business_units: ["US"] },
};

export interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
}

export type Handler = (call: Call) => Response | Promise<Response>;

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function fakeFetch(handler: Handler) {
  const calls: Call[] = [];
  const fetch: FetchLike = async (input, init) => {
    const call: Call = {
      method: init?.method ?? "GET",
      path: input,
      headers: Object.fromEntries(new Headers(init?.headers).entries()),
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    return handler(call);
  };
  return { fetch, calls };
}

const fakeBackend: AuthBackend = {
  kind: "test",
  init: async () => "signed_in",
  getAccessToken: async () => "test-token",
  onUnauthorized: async () => true,
  switchUser: async () => {},
  signOut: async () => {},
};

export function renderWithApp(
  ui: ReactNode,
  { persona = "dave", handler, path = "/", route = "/" }: { persona?: string; handler: Handler; path?: string; route?: string },
) {
  const me = PERSONAS[persona]!;
  const { fetch, calls } = fakeFetch(handler);
  const api = new ApiClient({ getAccessToken: async () => "test-token", fetch, onUnauthorized: vi.fn() });
  const roles = new Set<Role>(me.roles);
  const auth: AuthContextValue = {
    me,
    backend: fakeBackend,
    hasAnyRole: (...rs) => rs.some((r) => roles.has(r)),
    switchUser: async () => {},
    signOut: async () => {},
  };
  const utils = render(
    <ApiContext.Provider value={api}>
      <AuthContext.Provider value={auth}>
        <ToastProvider>
          <MemoryRouter initialEntries={[route]}>
            <Routes>
              <Route path={path} element={ui} />
              <Route path="*" element={<div data-testid="navigated" />} />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </AuthContext.Provider>
    </ApiContext.Provider>,
  );
  return { ...utils, calls };
}
