import type { ReactNode } from "react";

export type Tone = "neutral" | "info" | "warning" | "success" | "danger" | "accent";

const TONES: Record<string, Tone> = {
  pending_approval: "warning",
  approved: "info",
  processing: "accent",
  succeeded: "success",
  failed: "danger",
  rejected: "neutral",
};

export function humanize(value: string): string {
  const s = value.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function StatusBadge({ status, tone }: { status: string; tone?: Tone }) {
  const t = tone ?? TONES[status] ?? "neutral";
  return (
    <span className={`badge badge-${t}`} data-status={status}>
      <span className="badge-dot" aria-hidden />
      {humanize(status)}
    </span>
  );
}

export function Chip({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  return <span className={`chip chip-${tone}`}>{children}</span>;
}
