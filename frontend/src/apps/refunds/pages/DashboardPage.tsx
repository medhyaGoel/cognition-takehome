import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { useApi } from "../../../platform/api/context";
import { useAuth } from "../../../platform/auth/AuthProvider";
import { Can } from "../../../platform/auth/Can";
import { Card, PageHeader } from "../../../platform/components/AppShell";
import { DataTable, type Column } from "../../../platform/components/DataTable";
import { Money } from "../../../platform/components/Money";
import { Chip, StatusBadge } from "../../../platform/components/StatusBadge";
import { EmptyState, ErrorState, SkeletonRows } from "../../../platform/components/States";
import { useQuery } from "../../../platform/hooks/useQuery";
import { formatCents } from "../../../platform/money";
import { ageLabel, formatDateTime, formatDuration } from "../../../platform/time";
import { fetchRecentRefunds, fetchRefundDetails } from "../api";
import {
  approvedByHumans,
  CHART_GROUPS,
  computeDashboard,
  median,
  timeToApproveMs,
  type CurrencyTotals,
  type DayBucket,
} from "../dashboard";
import type { Refund } from "../types";
import { shortId } from "./QueuePage";

export const DASHBOARD_WINDOW = 500;

function Totals({ totals }: { totals: CurrencyTotals }) {
  const entries = Object.entries(totals).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) return <span className="muted">{formatCents(0, "USD")}</span>;
  return (
    <span className="kpi-totals">
      {entries.map(([cur, cents]) => (
        <span key={cur}>{formatCents(cents, cur)}</span>
      ))}
    </span>
  );
}

function KpiCard({
  label,
  value,
  sub,
  tone,
  to,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone: "warning" | "info" | "success" | "danger" | "accent";
  to?: string;
}) {
  const body = (
    <>
      <div className="kpi-label">
        <span className={`kpi-dot tone-${tone}`} aria-hidden />
        {label}
      </div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </>
  );
  return to ? (
    <Link to={to} className="kpi card">
      {body}
    </Link>
  ) : (
    <div className="kpi card">{body}</div>
  );
}

function DayChart({ data }: { data: DayBucket[] }) {
  const max = Math.max(1, ...data.map((d) => d.total));
  const W = 640;
  const H = 180;
  const pad = { l: 28, r: 8, t: 8, b: 24 };
  const bw = (W - pad.l - pad.r) / data.length;
  const y = (v: number) => pad.t + (H - pad.t - pad.b) * (1 - v / max);
  const ticks = Array.from(new Set([0, Math.ceil(max / 2), max]));
  const fmtDay = (d: string) => {
    const [, m, day] = d.split("-");
    return `${Number(m)}/${Number(day)}`;
  };
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Refunds created per day, last 14 days, by status">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className="chart-grid" />
            <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" className="chart-axis">
              {t}
            </text>
          </g>
        ))}
        {data.map((d, i) => {
          let acc = 0;
          const x = pad.l + i * bw + bw * 0.18;
          return (
            <g key={d.day}>
              <title>
                {`${d.day}: ${d.total} refund${d.total === 1 ? "" : "s"}\n` +
                  CHART_GROUPS.map((g) => `${g.label}: ${d.counts[g.key]}`).join("\n")}
              </title>
              {CHART_GROUPS.map((g) => {
                const v = d.counts[g.key];
                if (!v) return null;
                const y1 = y(acc + v);
                const h = y(acc) - y1;
                acc += v;
                return (
                  <rect key={g.key} x={x} y={y1} width={bw * 0.64} height={h} rx={2} className={`bar bar-${g.key}`} />
                );
              })}
              {(i % 2 === data.length % 2 || data.length <= 7) && (
                <text x={x + bw * 0.32} y={H - 6} textAnchor="middle" className="chart-axis">
                  {fmtDay(d.day)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="legend">
        {CHART_GROUPS.map((g) => (
          <span key={g.key} className="legend-item">
            <span className={`legend-swatch bar-${g.key}`} aria-hidden />
            {g.label}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

const TIME_TO_APPROVE_SAMPLE = 50;

export function DashboardPage() {
  const api = useApi();
  const navigate = useNavigate();
  const { me, hasAnyRole } = useAuth();
  const [bu, setBu] = useState<string | null>(null);
  const q = useQuery((s) => fetchRecentRefunds(api, DASHBOARD_WINDOW, s), [api]);

  const canApprove = hasAnyRole("refund_approver", "finance_admin");
  const stats = useMemo(
    () =>
      q.data
        ? computeDashboard(q.data.rows, { businessUnit: bu, subject: me.subject, canApprove })
        : null,
    [q.data, bu, me.subject, canApprove],
  );

  // Decision timestamps only exist on RefundDetail.approvals, so fetch details for the most
  // recent human-approved refunds in the window (bounded, newest first).
  const approvalIds = useMemo(
    () => (q.data ? approvedByHumans(q.data.rows).slice(0, TIME_TO_APPROVE_SAMPLE).map((r) => r.id) : []),
    [q.data],
  );
  const tta = useQuery(
    async (s) => {
      const details = await fetchRefundDetails(api, approvalIds, s);
      return details.flatMap((d) => {
        const ms = timeToApproveMs(d);
        return ms === null ? [] : [{ bu: d.business_unit, ms }];
      });
    },
    [api, approvalIds],
  );
  const ttaSamples = tta.data?.filter((x) => bu === null || x.bu === bu).map((x) => x.ms) ?? [];
  const ttaMedian = median(ttaSamples);

  const oldest = q.data?.rows.at(-1)?.created_at;
  const windowLabel = !q.data
    ? null
    : q.data.truncated
      ? `Based on the ${q.data.rows.length} most recent refunds${oldest ? ` (since ${formatDateTime(oldest)})` : ""}.`
      : `Based on all ${q.data.rows.length} refunds visible to you.`;

  const attentionCols: Column<Refund>[] = [
    { key: "id", header: "Refund", render: (r) => <code className="mono">{shortId(r.id)}</code> },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      render: (r) => <Money cents={r.amount_cents} currency={r.currency} />,
      sortValue: (r) => r.amount_cents,
    },
    { key: "bu", header: "BU", render: (r) => <Chip tone="accent">{r.business_unit}</Chip> },
    { key: "requester", header: "Requester", render: (r) => r.requested_by.split("@")[0] },
    {
      key: "age",
      header: "Waiting",
      align: "right",
      render: (r) => (
        <span title={formatDateTime(r.created_at)} className={r.escalated_at ? "text-danger" : undefined}>
          {ageLabel(r.created_at)}
          {r.escalated_at ? " · escalated" : ""}
        </span>
      ),
      sortValue: (r) => Date.parse(r.created_at),
    },
  ];

  const buOptions = me.business_units;

  return (
    <>
      <PageHeader
        title="Refunds overview"
        subtitle={
          <>
            Welcome back, {me.display_name}. {windowLabel && <span className="muted">{windowLabel}</span>}
          </>
        }
        actions={
          <>
            <div className="segmented" role="group" aria-label="Business unit">
              <button type="button" className={bu === null ? "active" : ""} aria-pressed={bu === null} onClick={() => setBu(null)}>
                All units
              </button>
              {buOptions.map((b) => (
                <button key={b} type="button" className={bu === b ? "active" : ""} aria-pressed={bu === b} onClick={() => setBu(b)}>
                  {b}
                </button>
              ))}
            </div>
            <Can roles={["refund_agent", "finance_admin"]}>
              <Link className="btn btn-primary" to="/refunds/new">
                New refund
              </Link>
            </Can>
          </>
        }
      />

      {q.error ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        </Card>
      ) : !stats ? (
        <>
          <div className="kpi-grid">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="kpi card skeleton-card" />
            ))}
          </div>
          <Card>
            <SkeletonRows rows={5} />
          </Card>
        </>
      ) : (
        <>
          <div className="kpi-grid">
            <KpiCard
              label="Pending approval"
              tone="warning"
              value={stats.pending.count}
              sub={<Totals totals={stats.pending.totals} />}
              to="/refunds/queue?status=pending_approval"
            />
            <KpiCard
              label="Approved, awaiting payout"
              tone="info"
              value={stats.awaitingPayout.count}
              sub={<Totals totals={stats.awaitingPayout.totals} />}
              to="/refunds/queue?status=approved"
            />
            <KpiCard
              label="Refunded · last 7 days"
              tone="success"
              value={<Totals totals={stats.refunded7d.totals} />}
              sub={`${stats.refunded7d.count} refund${stats.refunded7d.count === 1 ? "" : "s"}`}
            />
            <KpiCard
              label="Failed refunds"
              tone="danger"
              value={stats.failed.count}
              sub={<Totals totals={stats.failed.totals} />}
              to="/refunds/queue?status=failed"
            />
            <KpiCard
              label="Median time to approve"
              tone="accent"
              value={tta.loading && !tta.data ? "…" : ttaMedian === null ? "—" : formatDuration(ttaMedian)}
              sub={
                tta.error
                  ? "Couldn't load approval times"
                  : tta.loading && !tta.data
                    ? "Loading approval times…"
                    : ttaSamples.length === 0
                      ? "No approved refunds in this window yet"
                      : `Request to final approval · ${ttaSamples.length} approved refund${ttaSamples.length === 1 ? "" : "s"}${
                          approvalIds.length === TIME_TO_APPROVE_SAMPLE ? " (most recent)" : ""
                        }`
              }
            />
          </div>

          <div className="grid-dash">
            <Card title="Refunds created · last 14 days">
              {stats.byDay.every((d) => d.total === 0) ? (
                <EmptyState title="No refunds in the last 14 days" />
              ) : (
                <DayChart data={stats.byDay} />
              )}
            </Card>

            <Card
              title={
                <>
                  Needs my attention{" "}
                  {canApprove && <span className="count-pill">{stats.attention.length}</span>}
                </>
              }
              actions={
                <Link to="/refunds/queue?status=pending_approval" className="small">
                  View queue →
                </Link>
              }
            >
              {!canApprove ? (
                <EmptyState
                  title="Nothing to approve"
                  description="Your role doesn't approve refunds. Approvers and finance admins see their queue here."
                />
              ) : (
                <DataTable
                  rows={stats.attention.slice(0, 8)}
                  columns={attentionCols}
                  rowKey={(r) => r.id}
                  onRowClick={(r) => navigate(`/refunds/${r.id}`)}
                  caption="Refunds awaiting your approval"
                  empty={
                    <EmptyState
                      title="Inbox zero"
                      description="No pending refunds from other requesters."
                    />
                  }
                />
              )}
              {canApprove && stats.attention.length > 0 && (
                <p className="muted small card-foot">
                  Pending refunds you didn't request, oldest first. Includes ones you may already have approved if
                  more approvals are required.
                </p>
              )}
            </Card>
          </div>

          <Card title="Recent activity" actions={<Link to="/refunds/queue" className="small">All refunds →</Link>}>
            <DataTable
              rows={(bu ? q.data!.rows.filter((r) => r.business_unit === bu) : q.data!.rows).slice(0, 6)}
              columns={[
                { key: "id", header: "Refund", render: (r) => <code className="mono">{shortId(r.id)}</code> },
                { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> },
                {
                  key: "amount",
                  header: "Amount",
                  align: "right",
                  render: (r) => <Money cents={r.amount_cents} currency={r.currency} />,
                },
                { key: "bu", header: "BU", render: (r) => <Chip tone="accent">{r.business_unit}</Chip> },
                { key: "requester", header: "Requester", render: (r) => r.requested_by },
                { key: "age", header: "Age", align: "right", render: (r) => ageLabel(r.created_at) },
              ]}
              rowKey={(r) => r.id}
              onRowClick={(r) => navigate(`/refunds/${r.id}`)}
              empty={<EmptyState title="No refunds yet" />}
            />
          </Card>
        </>
      )}
    </>
  );
}
