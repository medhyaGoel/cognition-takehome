import { describe, expect, it } from "vitest";
import { OidcAuthBackend, safeReturnPath } from "./oidc";

describe("safeReturnPath", () => {
  it("only restores same-origin relative paths", () => {
    expect(safeReturnPath("/refunds/abc?x=1#y")).toBe("/refunds/abc?x=1#y");
    for (const v of ["https://evil.test/", "//evil.test", "/\\evil.test", "javascript:alert(1)", "", null, 42, "/auth/callback?code=x"]) {
      expect(safeReturnPath(v)).toBe("/");
    }
  });
});

describe("OidcAuthBackend", () => {
  it("is generic OIDC code+PKCE with tokens and state in sessionStorage only", () => {
    const backend = new OidcAuthBackend(
      { issuer: "https://login.microsoftonline.com/tenant/v2.0", client_id: "web", audience: "api" },
      "http://localhost:5173",
    );
    const settings = (backend as unknown as { mgr: { settings: Record<string, unknown> } }).mgr.settings;
    expect(settings.authority).toBe("https://login.microsoftonline.com/tenant/v2.0");
    expect(settings.redirect_uri).toBe("http://localhost:5173/auth/callback");
    expect(settings.response_type).toBe("code");
    expect(settings.scope).toBe("openid profile email");
    const stores = [settings.userStore, settings.stateStore] as { _store: Storage }[];
    for (const s of stores) expect(s._store).toBe(window.sessionStorage);
  });
});
