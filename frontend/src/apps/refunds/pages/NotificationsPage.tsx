import { Link } from "react-router";
import { useApi } from "../../../platform/api/context";
import { Card, PageHeader } from "../../../platform/components/AppShell";
import { Chip } from "../../../platform/components/StatusBadge";
import { EmptyState, ErrorState, SkeletonRows } from "../../../platform/components/States";
import { useQuery } from "../../../platform/hooks/useQuery";
import { formatDateTime, relativeTime } from "../../../platform/time";
import { refundsApi } from "../api";

export function NotificationsPage() {
  const api = useApi();
  const q = useQuery((s) => refundsApi(api).notifications(50, s), [api]);
  return (
    <>
      <PageHeader
        title="Notifications"
        subtitle="Workflow notifications for your roles and business units, newest first."
        actions={
          <button type="button" className="btn btn-secondary" onClick={() => void q.refetch()} disabled={q.loading}>
            Refresh
          </button>
        }
      />
      <Card>
        {q.error ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        ) : !q.data ? (
          <SkeletonRows rows={4} />
        ) : q.data.length === 0 ? (
          <EmptyState title="You're all caught up" description="No notifications yet." />
        ) : (
          <ul className="notif-list">
            {q.data.map((n) => (
              <li key={n.id} className="notif">
                <div className="notif-head">
                  <strong>{n.title}</strong>
                  <Chip>{n.topic}</Chip>
                  {n.business_unit && <Chip tone="accent">{n.business_unit}</Chip>}
                  <span className="muted small notif-time" title={formatDateTime(n.created_at)}>
                    {relativeTime(n.created_at)}
                  </span>
                </div>
                <p className="notif-body">{n.body}</p>
                {n.entity_type === "refund" && n.entity_id && (
                  <Link to={`/refunds/${n.entity_id}`} className="small">
                    Open refund →
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
