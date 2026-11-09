export type ApiErrorKind =
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "validation"
  | "server"
  | "network";

/** Every failed request surfaces as an ApiError; `kind` drives UI handling, `code` is the server's. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly kind: ApiErrorKind;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.kind = kindForStatus(status);
  }

  /** True when the request may not have reached the server or its outcome is unknown. */
  get outcomeUnknown(): boolean {
    return this.kind === "network" || this.kind === "server";
  }
}

export function kindForStatus(status: number): ApiErrorKind {
  if (status === 0) return "network";
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "validation";
  return "server";
}

const DEFAULT_CODES: Record<ApiErrorKind, string> = {
  unauthorized: "unauthorized",
  forbidden: "forbidden",
  not_found: "not_found",
  conflict: "conflict",
  validation: "validation_failed",
  server: "server_error",
  network: "network_error",
};

/**
 * Maps the contract's error envelope `{"error": {code, message}}` and FastAPI's
 * `{"detail": string | [{msg}]}` into an ApiError.
 */
export function errorFromBody(status: number, body: unknown): ApiError {
  const fallbackCode = DEFAULT_CODES[kindForStatus(status)];
  if (isRecord(body)) {
    const env = body.error;
    if (isRecord(env) && typeof env.code === "string") {
      const message = typeof env.message === "string" ? env.message : env.code;
      return new ApiError(status, env.code, message);
    }
    const detail = body.detail;
    if (typeof detail === "string") return new ApiError(status, fallbackCode, detail);
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (isRecord(d) && typeof d.msg === "string" ? d.msg : null))
        .filter((m): m is string => m !== null);
      return new ApiError(status, fallbackCode, msgs.join("; ") || "Invalid input");
    }
  }
  return new ApiError(status, fallbackCode, `Request failed (HTTP ${status})`);
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
