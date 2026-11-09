import type { ReactNode } from "react";
import type { ApiError } from "../api/errors";

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state state-loading">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="skeleton-rows" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="state state-empty">
      <div className="state-icon" aria-hidden>
        ∅
      </div>
      <div className="state-title">{title}</div>
      {description && <div className="state-desc">{description}</div>}
      {action}
    </div>
  );
}

export function errorText(err: ApiError, notFound = "Not found or outside your business units") {
  switch (err.kind) {
    case "not_found":
      return notFound;
    case "forbidden":
      return `You don't have permission to do this. ${err.message}`;
    case "unauthorized":
      return "Your session expired. Signing you in again…";
    case "network":
      return "Can't reach the server. Check your connection and try again.";
    default:
      return err.message;
  }
}

export function ErrorState({
  error,
  onRetry,
  notFound,
}: {
  error: ApiError;
  onRetry?: () => void;
  notFound?: string;
}) {
  return (
    <div className="state state-error" role="alert">
      <div className="state-icon" aria-hidden>
        !
      </div>
      <div className="state-title">
        {error.kind === "not_found" ? "Not found" : "Something went wrong"}
      </div>
      <div className="state-desc">{errorText(error, notFound)}</div>
      {onRetry && error.kind !== "not_found" && error.kind !== "forbidden" && (
        <button className="btn btn-secondary" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Banner({
  tone,
  children,
  onDismiss,
}: {
  tone: "info" | "warning" | "danger" | "success";
  children: ReactNode;
  onDismiss?: () => void;
}) {
  return (
    <div className={`banner banner-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <div className="banner-body">{children}</div>
      {onDismiss && (
        <button className="btn-icon" aria-label="Dismiss" onClick={onDismiss}>
          ×
        </button>
      )}
    </div>
  );
}
