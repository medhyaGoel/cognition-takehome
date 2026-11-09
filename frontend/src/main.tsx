import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { loadAuthConfig, OidcAuthBackend } from "./platform/auth/oidc";
import "./platform/styles.css";

async function start() {
  return new OidcAuthBackend(await loadAuthConfig());
}

const root = createRoot(document.getElementById("root")!);
start()
  .then((backend) =>
    root.render(
      <StrictMode>
        <App backend={backend} />
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
