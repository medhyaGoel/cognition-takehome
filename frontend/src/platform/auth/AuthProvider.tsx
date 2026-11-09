import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiClient, type FetchLike } from "../api/client";
import { ApiContext } from "../api/context";
import type { ApiError } from "../api/errors";
import type { AuthBackend, Me, Role } from "./types";

export interface AuthContextValue {
  me: Me;
  backend: AuthBackend;
  hasAnyRole: (...roles: Role[]) => boolean;
  switchUser: () => Promise<void>;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

type Phase =
  | { status: "loading" }
  | { status: "redirecting" }
  | { status: "error"; message: string }
  | { status: "ready"; me: Me };

// React StrictMode runs effects twice; the OIDC callback must only be processed once.
const initOnce = new WeakMap<AuthBackend, Promise<"signed_in" | "redirecting">>();

export function AuthProvider({
  backend,
  children,
  fallback,
  transport,
}: {
  backend: AuthBackend;
  /** Optional fetch override (dev mocks, tests). */
  transport?: FetchLike;
  children: ReactNode;
  fallback: (phase: "loading" | "redirecting" | { error: string }) => ReactNode;
}) {
  const [phase, setPhase] = useState<Phase>({ status: "loading" });
  const api = useMemo(
    () =>
      new ApiClient({
        getAccessToken: () => backend.getAccessToken(),
        onUnauthorized: () => backend.onUnauthorized(),
        fetch: transport,
      }),
    [backend, transport],
  );

  useEffect(() => {
    let cancelled = false;
    let p = initOnce.get(backend);
    if (!p) {
      p = backend.init();
      initOnce.set(backend, p);
    }
    p.then(async (result) => {
      if (result === "redirecting") {
        if (!cancelled) setPhase({ status: "redirecting" });
        return;
      }
      const me = await api.get<Me>("/api/me");
      if (!cancelled) setPhase({ status: "ready", me });
    }).catch((err: unknown) => {
      if (!cancelled) setPhase({ status: "error", message: describe(err) });
    });
    return () => {
      cancelled = true;
    };
  }, [backend, api]);

  const value = useMemo<AuthContextValue | null>(() => {
    if (phase.status !== "ready") return null;
    const roles = new Set(phase.me.roles);
    return {
      me: phase.me,
      backend,
      hasAnyRole: (...rs) => rs.some((r) => roles.has(r)),
      switchUser: () => backend.switchUser(),
      signOut: () => backend.signOut(),
    };
  }, [phase, backend]);

  if (phase.status === "loading") return <>{fallback("loading")}</>;
  if (phase.status === "redirecting") return <>{fallback("redirecting")}</>;
  if (phase.status === "error") return <>{fallback({ error: phase.message })}</>;
  return (
    <ApiContext.Provider value={api}>
      <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
    </ApiContext.Provider>
  );
}

function describe(err: unknown): string {
  if (err && typeof err === "object" && "message" in err) {
    return String((err as ApiError).message);
  }
  return "Sign-in failed";
}
