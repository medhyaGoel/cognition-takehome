import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useApi } from "../../../platform/api/context";
import { ApiError } from "../../../platform/api/errors";
import { Can } from "../../../platform/auth/Can";
import { Card, PageHeader } from "../../../platform/components/AppShell";
import { DataTable, type Column } from "../../../platform/components/DataTable";
import { Money } from "../../../platform/components/Money";
import { Chip, StatusBadge, humanize } from "../../../platform/components/StatusBadge";
import { EmptyState, ErrorState, SkeletonRows } from "../../../platform/components/States";
import { useToast } from "../../../platform/components/Toast";
import { useQuery } from "../../../platform/hooks/useQuery";
import { ageLabel, formatDateTime } from "../../../platform/time";
import { refundsApi } from "../api";
import { REFUND_STATUSES, type Refund, type RefundStatus } from "../types";

const PAGE_SIZE = 50;

export function shortId(id: string): string {
  return id.slice(0, 8);
}

function isStatus(v: string | null): v is RefundStatus {
  return v !== null && (REFUND_STATUSES as string[]).includes(v);
}

export function QueuePage() {
  const api = useApi();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const statusParam = params.get("status");
  const status: RefundStatus | "" = isStatus(statusParam) ? statusParam : "";
  const page = Math.max(0, Number.parseInt(params.get("page") ?? "0", 10) || 0);

  const q = useQuery(
    (signal) => refundsApi(api).listRefunds({ status, limit: PAGE_SIZE, offset: page * PAGE_SIZE }, signal),
    [api, status, page],
  );

  const setFilter = (next: RefundStatus | "") => {
    const p = new URLSearchParams();
    if (next) p.set("status", next);
    setParams(p);
  };
  const setPage = (n: number) => {
    const p = new URLSearchParams(params);
    if (n > 0) p.set("page", String(n));
    else p.delete("page");
    setParams(p);
  };

  const columns = useMemo<Column<Refund>[]>(
    () => [
      {
        key: "id",
        header: "Refund",
        render: (r) => <code className="mono">{shortId(r.id)}</code>,
      },
      {
        key: "status",
        header: "Status",
        render: (r) => <StatusBadge status={r.status} />,
        sortValue: (r) => r.status,
      },
      {
        key: "amount",
        header: "Amount",
        align: "right",
        render: (r) => <Money cents={r.amount_cents} currency={r.currency} />,
        sortValue: (r) => r.amount_cents,
      },
      {
        key: "payment",
        header: "Payment",
        render: (r) => <code className="mono muted">{shortId(r.payment_id)}</code>,
      },
      {
        key: "bu",
        header: "BU",
        render: (r) => <Chip tone="accent">{r.business_unit}</Chip>,
        sortValue: (r) => r.business_unit,
      },
      {
        key: "requester",
        header: "Requester",
        render: (r) => r.requested_by,
        sortValue: (r) => r.requested_by,
      },
      {
        key: "approvals",
        header: "Approvals",
        render: (r) => (
          <span className={r.approvals_count >= r.required_approvals ? "text-success" : undefined}>
            {r.approvals_count}/{r.required_approvals}
          </span>
        ),
        sortValue: (r) => r.required_approvals - r.approvals_count,
      },
      {
        key: "age",
        header: "Age",
        align: "right",
        render: (r) => <span title={formatDateTime(r.created_at)}>{ageLabel(r.created_at)}</span>,
        sortValue: (r) => -Date.parse(r.created_at),
      },
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Refund queue"
        subtitle="All refunds in your business units, newest first."
        actions={
          <>
            <Can roles={["finance_admin"]}>
              <ExportButton status={status} />
            </Can>
            <Can roles={["refund_agent", "finance_admin"]}>
              <Link className="btn btn-primary" to="/refunds/new">
                New refund
              </Link>
            </Can>
          </>
        }
      />
      <Card>
        <div className="toolbar">
          <div className="segmented" role="group" aria-label="Filter by status">
            <button
              type="button"
              className={status === "" ? "active" : ""}
              aria-pressed={status === ""}
              onClick={() => setFilter("")}
            >
              All
            </button>
            {REFUND_STATUSES.map((s) => (
              <button
                key={s}
                type="button"
                className={status === s ? "active" : ""}
                aria-pressed={status === s}
                onClick={() => setFilter(s)}
              >
                {humanize(s)}
              </button>
            ))}
          </div>
          <span className="muted small">Sorting applies to the loaded page</span>
        </div>
        {q.error ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        ) : !q.data ? (
          <SkeletonRows rows={6} />
        ) : (
          <>
            <DataTable
              rows={q.data}
              columns={columns}
              rowKey={(r) => r.id}
              onRowClick={(r) => navigate(`/refunds/${r.id}`)}
              caption="Refunds"
              empty={
                <EmptyState
                  title="No refunds"
                  description={
                    status ? `Nothing with status “${humanize(status)}”.` : "No refunds have been requested yet."
                  }
                />
              }
            />
            <div className="pager">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={page === 0 || q.loading}
                onClick={() => setPage(page - 1)}
              >
                ← Newer
              </button>
              <span className="muted small">Page {page + 1}</span>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={q.data.length < PAGE_SIZE || q.loading}
                onClick={() => setPage(page + 1)}
              >
                Older →
              </button>
            </div>
          </>
        )}
      </Card>
    </>
  );
}

function ExportButton({ status }: { status: RefundStatus | "" }) {
  const api = useApi();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const download = async () => {
    setBusy(true);
    try {
      const res = await refundsApi(api).exportCsv(status);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `refunds${status ? `-${status}` : ""}-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.push("danger", err instanceof ApiError ? `Export failed: ${err.message}` : "Export failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void download()}>
      {busy ? "Exporting…" : "Export CSV"}
    </button>
  );
}
