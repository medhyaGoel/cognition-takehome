import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";
import type { AuthBackend, AuthConfig } from "./types";

export const CALLBACK_PATH = "/auth/callback";

/** Only same-origin relative paths may be restored after login (no open redirects). */
export function safeReturnPath(value: unknown): string {
  if (typeof value !== "string") return "/";
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  if (value.startsWith(CALLBACK_PATH)) return "/";
  return value;
}

interface LoginState {
  returnTo: string;
}

/**
 * Generic OIDC Authorization Code + PKCE. Nothing here is Keycloak-specific: pointing
 * /api/auth/config at Entra ID (issuer https://login.microsoftonline.com/<tenant>/v2.0) is enough.
 * Tokens live in sessionStorage (per tab, cleared on close), never localStorage.
 */
export class OidcAuthBackend implements AuthBackend {
  readonly kind = "oidc" as const;
  private readonly mgr: UserManager;
  private user: User | null = null;
  private renewing: Promise<User | null> | null = null;
  private readonly listeners = new Set<() => void>();

  constructor(config: AuthConfig, origin = window.location.origin) {
    const store = new WebStorageStateStore({ store: window.sessionStorage });
    this.mgr = new UserManager({
      authority: config.issuer,
      client_id: config.client_id,
      redirect_uri: `${origin}${CALLBACK_PATH}`,
      post_logout_redirect_uri: `${origin}/`,
      response_type: "code",
      scope: "openid profile email",
      userStore: store,
      stateStore: store,
      automaticSilentRenew: true,
      // Renew with the refresh token (no hidden iframe / third-party cookies needed).
      silentRequestTimeoutInSeconds: 10,
      monitorSession: false,
    });
    this.mgr.events.addUserLoaded((u) => this.setUser(u));
    this.mgr.events.addUserUnloaded(() => this.setUser(null));
    this.mgr.events.addSilentRenewError(() => {
      // Refresh token expired or revoked: fall back to an interactive login.
      void this.login();
    });
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async init(): Promise<"signed_in" | "redirecting"> {
    if (window.location.pathname === CALLBACK_PATH) {
      const user = await this.mgr.signinRedirectCallback();
      this.setUser(user);
      const state = user.state as Partial<LoginState> | undefined;
      window.history.replaceState(null, "", safeReturnPath(state?.returnTo));
      return "signed_in";
    }
    const existing = await this.mgr.getUser();
    if (existing && !existing.expired) {
      this.setUser(existing);
      return "signed_in";
    }
    if (existing?.refresh_token) {
      const renewed = await this.renew();
      if (renewed) return "signed_in";
    }
    await this.login();
    return "redirecting";
  }

  async getAccessToken(): Promise<string | null> {
    if (this.user && !this.user.expired) return this.user.access_token;
    const renewed = await this.renew();
    return renewed?.access_token ?? null;
  }

  async onUnauthorized(): Promise<boolean> {
    // Token rejected (expired early, key rotated, ...). Try one silent renew, else re-login.
    const u = await this.renew();
    if (!u) void this.login();
    return u !== null;
  }

  async switchUser(): Promise<void> {
    // Ends the IdP session too, so the next sign-in shows the IdP's login page.
    await this.mgr.signoutRedirect({ id_token_hint: this.user?.id_token });
  }

  async signOut(): Promise<void> {
    await this.switchUser();
  }

  private login(): Promise<void> {
    const returnTo = safeReturnPath(
      window.location.pathname + window.location.search + window.location.hash,
    );
    return this.mgr.signinRedirect({ state: { returnTo } satisfies LoginState });
  }

  private renew(): Promise<User | null> {
    this.renewing ??= this.mgr
      .signinSilent()
      .catch(() => null)
      .then((u) => {
        this.renewing = null;
        if (u) this.setUser(u);
        return u;
      });
    return this.renewing;
  }

  private setUser(u: User | null) {
    this.user = u;
    for (const l of this.listeners) l();
  }
}

export async function loadAuthConfig(fetchImpl: typeof fetch = fetch): Promise<AuthConfig> {
  const res = await fetchImpl("/api/auth/config", { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`Could not load sign-in configuration (HTTP ${res.status})`);
  const cfg = (await res.json()) as Partial<AuthConfig>;
  if (typeof cfg.issuer !== "string" || typeof cfg.client_id !== "string") {
    throw new Error("Sign-in configuration is incomplete");
  }
  return { issuer: cfg.issuer, client_id: cfg.client_id, audience: cfg.audience ?? "" };
}
