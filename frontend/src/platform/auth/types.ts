export type Role =
  | "viewer"
  | "refund_agent"
  | "refund_approver"
  | "finance_admin"
  | "flag_editor"
  | "flag_approver";

/** GET /api/me — the server's view of the caller. UI decisions use this, never raw token claims. */
export interface Me {
  subject: string;
  display_name: string;
  roles: Role[];
  business_units: string[];
}

export interface AuthConfig {
  issuer: string;
  client_id: string;
  audience: string;
}

export interface Persona {
  username: string;
  label: string;
}

/**
 * Pluggable sign-in mechanism (any OIDC provider: Keycloak locally, Entra ID in production).
 * Tests supply a fake.
 */
export interface AuthBackend {
  readonly kind: string;
  /** Resolves when the user is signed in. May navigate away to the IdP instead of resolving. */
  init(): Promise<"signed_in" | "redirecting">;
  getAccessToken(): Promise<string | null>;
  /** Called on a 401. Resolves true if credentials were renewed; false if it fell back to re-login. */
  onUnauthorized(): Promise<boolean>;
  /** Ends the IdP session and starts a fresh sign-in (lets the user pick another account). */
  switchUser(): Promise<void>;
  signOut(): Promise<void>;
  /** Subscribe to user changes (e.g. silent renew). Returns an unsubscribe fn. */
  subscribe?(listener: () => void): () => void;
}

export const DEMO_USERS: Persona[] = [
  { username: "alice", label: "agent · US" },
  { username: "frank", label: "agent · EU" },
  { username: "bob", label: "approver · US" },
  { username: "carol", label: "approver · US+EU" },
  { username: "dave", label: "finance admin · US+EU" },
  { username: "erin", label: "viewer · US" },
];
