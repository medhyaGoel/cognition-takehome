import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { loadAuthConfig, OidcAuthBackend } from "./platform/auth/oidc";
import type { FetchLike } from "./platform/api/client";
import "./platform/styles.css";

async function createTransport(): Promise<FetchLike | undefined> {
  // Statically false in normal builds, so the mock module is tree-shaken out.
  if (import.meta.env.VITE_USE_MOCKS === "1") {
    const { withWorkflowMocks } = await import("./mocks/workflowMocks");
    return withWorkflowMocks((input, init) => fetch(input, init));
  }
  return undefined;
}

async function start() {
  const [config, transport] = await Promise.all([loadAuthConfig(), createTransport()]);
  return { backend: new OidcAuthBackend(config), transport };
}

const root = createRoot(document.getElementById("root")!);
start()
  .then(({ backend, transport }) =>
    root.render(
      <StrictMode>
        <App backend={backend} transport={transport} />
      </StrictMode>,
    ),
  )
  .catch((err: unknown) => {
    root.render(
      <div className="fullpage">
        <div className="state state-error" role="alert">
          <div className="state-title">Can't start sign-in</div>
          <div className="state-desc">{err instanceof Error ? err.message : String(err)}</div>
          <button className="btn btn-primary" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </div>,
    );
  });
