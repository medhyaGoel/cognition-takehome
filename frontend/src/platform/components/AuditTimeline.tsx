import { formatDateTime, relativeTime } from "../time";

export interface AuditEntry {
  id: string | number;
  occurred_at: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  request_id: string | null;
}

function changes(before: AuditEntry["before"], after: AuditEntry["after"]) {
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]);
  const out: { key: string; from: unknown; to: unknown }[] = [];
  for (const key of keys) {
    const from = before?.[key];
    const to = after?.[key];
    if (JSON.stringify(from) !== JSON.stringify(to)) out.push({ key, from, to });
  }
  return out;
}

function show(v: unknown): string {
  if (v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

export function AuditTimeline({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0) return <p className="muted">No audit entries yet.</p>;
  return (
    <ol className="timeline">
      {entries.map((e) => {
        const diff = changes(e.before, e.after);
        return (
          <li key={e.id} className="timeline-item">
            <span className="timeline-dot" aria-hidden />
            <div className="timeline-content">
              <div className="timeline-head">
                <code className="timeline-action">{e.action}</code>
                <span className="muted">by</span>
                <span className="timeline-actor">{e.actor}</span>
                <time className="muted timeline-time" dateTime={e.occurred_at} title={formatDateTime(e.occurred_at)}>
                  {relativeTime(e.occurred_at)}
                </time>
              </div>
              {diff.length > 0 && (
                <dl className="timeline-diff">
                  {diff.map((d) => (
                    <div key={d.key} className="timeline-diff-row">
                      <dt>{d.key}</dt>
                      <dd>
                        {e.before && d.key in e.before && (
                          <>
                            <span className="diff-from">{show(d.from)}</span>
                            <span aria-hidden> → </span>
                          </>
                        )}
                        <span className="diff-to">{show(d.to)}</span>
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
              {e.request_id && <div className="timeline-req muted">request {e.request_id}</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
