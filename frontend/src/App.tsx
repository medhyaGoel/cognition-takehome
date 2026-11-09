import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { refundsRoutes } from "./apps/refunds/routes";
import { AuthProvider } from "./platform/auth/AuthProvider";
import type { FetchLike } from "./platform/api/client";
import type { AuthBackend } from "./platform/auth/types";
import { AppShell } from "./platform/components/AppShell";
import { EmptyState, LoadingState } from "./platform/components/States";
import { ToastProvider } from "./platform/components/Toast";

function FullPage({ children }: { children: ReactNode }) {
  return <div className="fullpage">{children}</div>;
}

export function App({ backend, transport }: { backend: AuthBackend; transport?: FetchLike }) {
  return (
    <AuthProvider
      backend={backend}
      transport={transport}
      fallback={(phase) => (
        <FullPage>
          {phase === "loading" ? (
            <LoadingState label="Signing you in…" />
          ) : phase === "redirecting" ? (
            <LoadingState label="Redirecting to your identity provider…" />
          ) : (
            <div className="state state-error" role="alert">
              <div className="state-title">Sign-in failed</div>
              <div className="state-desc">{phase.error}</div>
              <button className="btn btn-primary" onClick={() => window.location.assign("/")}>
                Try again
              </button>
            </div>
          )}
        </FullPage>
      )}
    >
      <ToastProvider>
        <BrowserRouter>
          <AppShell>
            <Routes>
              <Route index element={<Navigate to="/refunds" replace />} />
              {refundsRoutes}
              <Route
                path="*"
                element={<EmptyState title="Page not found" description="Pick an app from the sidebar." />}
              />
            </Routes>
          </AppShell>
        </BrowserRouter>
      </ToastProvider>
    </AuthProvider>
  );
}
