import { ApiError, errorFromBody } from "./errors";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface ApiClientOptions {
  /** Returns a current access token (renewing it if needed), or null when signed out. */
  getAccessToken: () => Promise<string | null>;
  /** Called once per 401 so the auth layer can renew or re-login. */
  onUnauthorized?: () => void;
  fetch?: FetchLike;
  baseUrl?: string;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  query?: Record<string, string | number | undefined | null>;
  signal?: AbortSignal;
}

export interface ApiResponse<T> {
  status: number;
  data: T;
}

/** Typed JSON client: bearer auth, contract error mapping, no retries (callers decide). */
export class ApiClient {
  private readonly opts: ApiClientOptions;
  private readonly fetchImpl: FetchLike;

  constructor(opts: ApiClientOptions) {
    this.opts = opts;
    this.fetchImpl = opts.fetch ?? ((input, init) => globalThis.fetch(input, init));
  }

  async get<T>(path: string, opts: Omit<RequestOptions, "method" | "body"> = {}): Promise<T> {
    return (await this.request<T>(path, { ...opts, method: "GET" })).data;
  }

  async post<T>(path: string, body: unknown, opts: Omit<RequestOptions, "method" | "body"> = {}) {
    return this.request<T>(path, { ...opts, method: "POST", body });
  }

  async request<T>(path: string, opts: RequestOptions = {}): Promise<ApiResponse<T>> {
    const res = await this.raw(path, opts);
    if (res.status === 204) return { status: res.status, data: undefined as T };
    const body = await readJson(res);
    return { status: res.status, data: body as T };
  }

  /** Authenticated request returning the raw Response (e.g. CSV). Throws ApiError on non-2xx. */
  async raw(path: string, opts: RequestOptions = {}): Promise<Response> {
    const token = await this.opts.getAccessToken();
    const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
    if (token) headers.Authorization = `Bearer ${token}`;
    let body: string | undefined;
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
    let res: Response;
    try {
      res = await this.fetchImpl(this.url(path, opts.query), {
        method: opts.method ?? "GET",
        headers,
        body,
        signal: opts.signal,
        credentials: "omit",
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") throw err;
      throw new ApiError(0, "network_error", "Network error: could not reach the server.");
    }
    if (!res.ok) {
      const err = errorFromBody(res.status, await readJson(res).catch(() => null));
      if (err.kind === "unauthorized") this.opts.onUnauthorized?.();
      throw err;
    }
    return res;
  }

  private url(path: string, query?: RequestOptions["query"]): string {
    const base = this.opts.baseUrl ?? "";
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query ?? {})) {
      if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
    }
    const qs = params.toString();
    return `${base}${path}${qs ? `?${qs}` : ""}`;
  }
}

async function readJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

/** Accepts either a bare array or a `{items: [...]}` envelope for list endpoints. */
export function asList<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];
  if (typeof data === "object" && data !== null) {
    for (const key of ["items", "results", "data", "rules"]) {
      const v = (data as Record<string, unknown>)[key];
      if (Array.isArray(v)) return v as T[];
    }
  }
  return [];
}
