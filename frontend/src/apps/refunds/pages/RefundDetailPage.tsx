import { useState } from "react";
import { Link, useParams } from "react-router";
import { useApi } from "../../../platform/api/context";
import { ApiError } from "../../../platform/api/errors";
import { useAuth } from "../../../platform/auth/AuthProvider";
import { Can } from "../../../platform/auth/Can";
import { AuditTimeline } from "../../../platform/components/AuditTimeline";
import { Card, PageHeader } from "../../../platform/components/AppShell";
import { ConfirmDialog } from "../../../platform/components/ConfirmDialog";
import { Money } from "../../../platform/components/Money";
import { Chip, StatusBadge, humanize } from "../../../platform/components/StatusBadge";
import { Banner, ErrorState, LoadingState, SkeletonRows, errorText } from "../../../platform/components/States";
import { useToast } from "../../../platform/components/Toast";
import { useQuery } from "../../../platform/hooks/useQuery";
import { formatDateTime, relativeTime } from "../../../platform/time";
import { refundsApi } from "../api";
import type { RefundDetail } from "../types";

export const STALE_MESSAGE = "This refund changed since you loaded it. We've reloaded the latest version.";

type Decision = "approve" | "reject";

export function RefundDetailPage() {
  const { id = "" } = useParams();
  const api = useApi();
  const { me } = useAuth();
  const toast = useToast();
  const refund = useQuery((s) => refundsApi(api).getRefund(id, s), [api, id]);
  const audit = useQuery((s) => refundsApi(api).getAudit(id, s), [api, id]);

  const [dialog, setDialog] = useState<Decision | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<{ tone: "warning" | "danger"; text: string } | null>(null);

  if (refund.error) {
    return (
      <>
        <BackLink />
        <ErrorState error={refund.error} onRetry={() => void refund.refetch()} />
      </>
    );
  }
  if (!refund.data) return <LoadingState label="Loading refund…" />;
  const r: RefundDetail = refund.data;

  const isRequester = r.requested_by.toLowerCase() === me.subject.toLowerCase();
  const alreadyDecided = r.approvals.some((a) => a.approver.toLowerCase() === me.subject.toLowerCase());
  const actionable = r.status === "pending_approval";

  const decide = async () => {
    if (!dialog || busy) return;
    setBusy(true);
    setBanner(null);
    try {
      if (dialog === "approve") await refundsApi(api).approve(r.id, r.version, text);
      else await refundsApi(api).reject(r.id, r.version, text);
      toast.push("success", dialog === "approve" ? "Approval recorded." : "Refund rejected.");
      setDialog(null);
      setText("");
    } catch (err) {
      if (err instanceof ApiError && err.kind === "conflict") {
        setBanner({ tone: "warning", text: STALE_MESSAGE });
        setDialog(null);
      } else if (err instanceof ApiError && err.kind === "forbidden") {
        setBanner({ tone: "danger", text: `Not allowed: ${err.message}` });
        setDialog(null);
      } else {
        setBanner({
          tone: "danger",
          text: err instanceof ApiError ? errorText(err) : "Unexpected error",
        });
      }
    } finally {
      setBusy(false);
      void refund.refetch();
      void audit.refetch();
    }
  };

  return (
    <>
      <BackLink />
      <PageHeader
        title={
          <span className="title-row">
            <Money cents={r.amount_cents} currency={r.currency} /> refund
            <StatusBadge status={r.status} />
            <Chip tone="accent">{r.business_unit}</Chip>
          </span>
        }
        subtitle={
          <>
            <code className="mono">{r.id}</code> · requested by {r.requested_by}{" "}
            <span title={formatDateTime(r.created_at)}>{relativeTime(r.created_at)}</span>
          </>
        }
        actions={
          actionable && (
            <Can roles={["refund_approver", "finance_admin"]}>
              {isRequester ? (
                <span className="muted small">You requested this refund, so you can't approve it.</span>
              ) : alreadyDecided ? (
                <span className="muted small">You've already recorded your decision.</span>
              ) : null}
              {!alreadyDecided && (
                <button type="button" className="btn btn-danger-outline" onClick={() => setDialog("reject")}>
                  Reject
                </button>
              )}
              {!isRequester && !alreadyDecided && (
                <button type="button" className="btn btn-primary" onClick={() => setDialog("approve")}>
                  Approve
                </button>
              )}
            </Can>
          )
        }
      />
      {banner && (
        <Banner tone={banner.tone} onDismiss={() => setBanner(null)}>
          {banner.text}
        </Banner>
      )}

      <div className="grid-detail">
        <div className="stack">
          <Card title="Refund">
            <dl className="kv">
              <dt>Reason</dt>
              <dd>{r.reason}</dd>
              <dt>Approvals</dt>
              <dd>
                {r.approvals_count} of {r.required_approvals} required
                {r.required_approvals === 0 && <span className="muted"> (auto-approved by rule)</span>}
              </dd>
              <dt>Version</dt>
              <dd className="mono">{r.version}</dd>
              {r.provider_refund_id && (
                <>
                  <dt>Provider refund</dt>
                  <dd className="mono">{r.provider_refund_id}</dd>
                </>
              )}
              {r.failure_reason && (
                <>
                  <dt>Failure</dt>
                  <dd className="text-danger">{r.failure_reason}</dd>
                </>
              )}
              {r.escalated_at && (
                <>
                  <dt>Escalated</dt>
                  <dd>{formatDateTime(r.escalated_at)}</dd>
                </>
              )}
              <dt>Updated</dt>
              <dd>{formatDateTime(r.updated_at)}</dd>
            </dl>
          </Card>

          <Card title={`Decisions (${r.approvals.length})`}>
            {r.approvals.length === 0 ? (
              <p className="muted">No decisions yet.</p>
            ) : (
              <ul className="decision-list">
                {r.approvals.map((a) => (
                  <li key={`${a.approver}-${a.created_at}`} className="decision">
                    <StatusBadge status={a.decision} tone={a.decision === "approved" ? "success" : "danger"} />
                    <span className="decision-who">{a.approver}</span>
                    <span className="muted small" title={formatDateTime(a.created_at)}>
                      {relativeTime(a.created_at)}
                    </span>
                    {a.comment && <p className="decision-comment">“{a.comment}”</p>}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Audit trail">
            {audit.error ? (
              <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
            ) : !audit.data ? (
              <SkeletonRows rows={3} />
            ) : (
              <AuditTimeline entries={audit.data} />
            )}
          </Card>
        </div>

        <Card title="Payment">
          <dl className="kv">
            <dt>Reference</dt>
            <dd className="mono">{r.payment.provider_ref}</dd>
            <dt>Customer</dt>
            <dd>{r.payment.customer_name}</dd>
            <dt>Email</dt>
            <dd>{r.payment.customer_email}</dd>
            <dt>Business unit</dt>
            <dd>
              <Chip tone="accent">{r.payment.business_unit}</Chip>
            </dd>
            <dt>Captured</dt>
            <dd>{formatDateTime(r.payment.captured_at)}</dd>
            <dt>Charged</dt>
            <dd>
              <Money cents={r.payment.amount_cents} currency={r.payment.currency} />
            </dd>
            <dt>Reserved</dt>
            <dd>
              <Money cents={r.payment.reserved_cents} currency={r.payment.currency} />
            </dd>
            <dt>Refunded</dt>
            <dd>
              <Money cents={r.payment.refunded_cents} currency={r.payment.currency} />
            </dd>
            <dt>Refundable</dt>
            <dd>
              <strong>
                <Money cents={r.payment.refundable_cents} currency={r.payment.currency} />
              </strong>
            </dd>
          </dl>
        </Card>
      </div>

      <ConfirmDialog
        open={dialog !== null}
        title={dialog === "approve" ? "Approve refund" : "Reject refund"}
        confirmLabel={dialog === "approve" ? "Approve" : "Reject"}
        tone={dialog === "reject" ? "danger" : "primary"}
        busy={busy}
        confirmDisabled={dialog === "reject" && text.trim().length === 0}
        onConfirm={() => void decide()}
        onCancel={() => {
          setDialog(null);
          setText("");
        }}
      >
        <p>
          {humanize(dialog ?? "")} <Money cents={r.amount_cents} currency={r.currency} /> to {r.payment.customer_name}{" "}
          (version {r.version}).
        </p>
        <label className="field">
          <span className="label">{dialog === "reject" ? "Reason (required)" : "Comment (optional)"}</span>
          <textarea
            className="input"
            rows={3}
            maxLength={1000}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
      </ConfirmDialog>
    </>
  );
}

function BackLink() {
  return (
    <Link className="back-link" to="/refunds/queue">
      ← Back to queue
    </Link>
  );
}
