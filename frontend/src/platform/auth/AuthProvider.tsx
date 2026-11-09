import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { ApiClient, type FetchLike } from "../api/client";
import { ApiContext } from "../api/context";
import { ApiError } from "../api/errors";
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
const MAX_RENEWALS_WITHOUT_ME = 2;

const initOnce = new WeakMap<AuthBackend, Promise<"signed_in" | "redirecting">>();

export function AuthProvider({
  backend,
  children,
  fallback,
  transport,
}: {
  backend: AuthBackend;
  /** Optional fetch override (tests). */
  transport?: FetchLike;
  children: ReactNode;
  fallback: (phase: "loading" | "redirecting" | { error: string }) => ReactNode;
}) {
  const [phase, setPhase] = useState<Phase>({ status: "loading" });

  const session = useMemo(() => createSession(backend, transport, setPhase), [backend, transport]);
  const { api } = session;

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
      let me: Me;
      try {
        me = await session.loadMe();
      } catch (err) {
        // Wait for the renewal the client started for this 401, then retry once.
        if (!(err instanceof ApiError && err.kind === "unauthorized")) throw err;
        if (!(await session.renewalAfter401())) return;
        me = await session.loadMe();
      }
      if (!cancelled) setPhase({ status: "ready", me });
    }).catch((err: unknown) => {
      if (!cancelled) setPhase({ status: "error", message: describe(err) });
    });
    return () => {
      cancelled = true;
    };
  }, [backend, session]);

  // Silent refresh (or any other credential change): re-read the principal.
  useEffect(
    () =>
      backend.subscribe?.(() => {
        void session.refreshMe();
      }),
    [backend, session],
  );

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

// One renewal at a time; a successful renewal re-reads /api/me so roles/BUs stay current.
// Bounded: if /api/me keeps failing right after renewals, stop and show the error.
function createSession(
  backend: AuthBackend,
  transport: FetchLike | undefined,
  setPhase: Dispatch<SetStateAction<Phase>>,
) {
  let renewing: Promise<boolean> | null = null;
  let lastRenewal: Promise<boolean> | null = null;
  let meInFlight: Promise<Me> | null = null;
  let renewalsWithoutMe = 0;
  const api: ApiClient = new ApiClient({
    getAccessToken: () => backend.getAccessToken(),
    onUnauthorized: () => void renew(),
    fetch: transport,
  });
  const loadMe = (): Promise<Me> =>
    (meInFlight ??= api
      .get<Me>("/api/me")
      .then((me) => {
        renewalsWithoutMe = 0;
        return me;
      })
      .finally(() => {
        meInFlight = null;
      }));
  const refreshMe = () =>
    loadMe().then(
      (me) => setPhase((p) => (p.status === "ready" ? { status: "ready", me } : p)),
      () => {},
    );
  const renew = (): Promise<boolean> =>
    (lastRenewal = renewing ??= backend
      .onUnauthorized()
      .then((ok) => {
        if (ok && ++renewalsWithoutMe <= MAX_RENEWALS_WITHOUT_ME) {
          void refreshMe();
          return true;
        }
        setPhase({ status: "error", message: "Your session expired and could not be renewed." });
        return false;
      })
      .finally(() => {
        renewing = null;
      }));
  // The client starts a renewal on every 401 before rejecting; this is that renewal's outcome.
  const renewalAfter401 = () => lastRenewal ?? renew();
  return { api, loadMe, refreshMe, renewalAfter401 };
}

function describe(err: unknown): string {
  if (err && typeof err === "object" && "message" in err) {
    return String((err as { message: unknown }).message);
  }
  return "Sign-in failed";
}
