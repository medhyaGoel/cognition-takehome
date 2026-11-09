import { useCallback, useMemo, useState } from "react";
import { useApi } from "../../platform/api/context";
import { ApiError } from "../../platform/api/errors";
import { useAuth } from "../../platform/auth/AuthProvider";
import { Card, PageHeader } from "../../platform/components/AppShell";
import { AuditTimeline } from "../../platform/components/AuditTimeline";
import { ConfirmDialog } from "../../platform/components/ConfirmDialog";
import { DataTable, type Column } from "../../platform/components/DataTable";
import {
  EmptyState,
  ErrorState,
  SkeletonRows,
  errorText,
} from "../../platform/components/States";
import { Chip, StatusBadge } from "../../platform/components/StatusBadge";
import { useToast } from "../../platform/components/Toast";
import { useIdempotentSubmit } from "../../platform/hooks/useIdempotentSubmit";
import { useQuery } from "../../platform/hooks/useQuery";
import { ageLabel, formatDateTime } from "../../platform/time";
import {
  ENVIRONMENTS,
  cellKey,
  flagsApi,
  toRows,
  type ChangeRequest,
  type FlagRow,
  type FlagState,
} from "./api";

const message = (err: unknown) =>
  err instanceof ApiError ? errorText(err) : String(err);

export function FlagsPage() {
  const api = useApi();
  const toast = useToast();
  const { hasAnyRole, me } = useAuth();
  const canEdit = hasAnyRole("flag_editor");
  const canApprove = hasAnyRole("flag_approver");

  const states = useQuery((signal) => flagsApi(api).listStates(signal), [api]);
  const pending = useQuery((signal) => flagsApi(api).pending(signal), [api]);
  const [target, setTarget] = useState<FlagState | null>(null);
  const [selected, setSelected] = useState<FlagState | null>(null);
  const [deciding, setDeciding] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    await Promise.all([states.refetch(), pending.refetch()]);
  }, [states, pending]);

  const send = useCallback(
    (key: string) => flagsApi(api).toggle(target as FlagState, key),
    [api, target],
  );
  const toggle = useIdempotentSubmit(send);

  const confirmToggle = async () => {
    try {
      const res = await toggle.submit();
      if (!res) return;
      const cr = res.data;
      toast.push(
        cr.status === "pending" ? "info" : "success",
        cr.status === "pending"
          ? `Prod change requested for ${cr.flag_key} (${cr.business_unit}); waiting for an approver`
          : `${cr.flag_key} ${cr.desired_enabled ? "enabled" : "disabled"} in ${cr.environment} (${cr.business_unit})`,
      );
    } catch (err) {
      toast.push("danger", message(err));
    }
    setTarget(null);
    await refresh();
  };

  const decide = async (cr: ChangeRequest, decision: "approve" | "reject") => {
    setDeciding(cr.id);
    try {
      await flagsApi(api).decide(
        cr,
        decision,
        decision === "reject" ? "Rejected from flags admin" : undefined,
      );
      toast.push(
        "success",
        `Change to ${cr.flag_key} ${decision === "approve" ? "approved and applied" : "rejected"}`,
      );
    } catch (err) {
      toast.push("danger", message(err));
    } finally {
      setDeciding(null);
      await refresh();
    }
  };

  const grid = useMemo(() => toRows(states.data ?? []), [states.data]);
  const columns = useMemo<Column<FlagRow>[]>(
    () => [
      {
        key: "flag",
        header: "Flag",
        sortValue: (r) => r.key,
        render: (r) => (
          <div>
            <code className="mono">{r.key}</code>
            <div className="muted small">{r.description}</div>
          </div>
        ),
      },
      ...grid.businessUnits.flatMap((bu) =>
        ENVIRONMENTS.map<Column<FlagRow>>((env) => ({
          key: cellKey(bu, env),
          header: `${bu} · ${env}`,
          align: "right",
          render: (r) => {
            const s = r.cells[cellKey(bu, env)];
            if (!s) return <span className="muted">—</span>;
            return (
              <span className="row-actions">
                <button
                  type="button"
                  className={`btn btn-sm ${s.enabled ? "btn-primary" : "btn-secondary"}`}
                  aria-label={`${r.key} ${bu} ${env}: ${s.enabled ? "on" : "off"}`}
                  aria-pressed={s.enabled}
                  disabled={!canEdit}
                  title={
                    canEdit
                      ? `v${s.version} · click to turn ${s.enabled ? "off" : "on"}`
                      : "Read-only"
                  }
                  onClick={(e) => {
                    e.stopPropagation();
                    setTarget(s);
                  }}
                >
                  {s.enabled ? "On" : "Off"}
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  aria-label={`History of ${r.key} ${bu} ${env}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelected(s);
                  }}
                >
                  ⌚
                </button>
              </span>
            );
          },
        })),
      ),
    ],
    [grid.businessUnits, canEdit],
  );

  const pendingColumns = useMemo<Column<ChangeRequest>[]>(
    () => [
      {
        key: "flag",
        header: "Flag",
        render: (c) => <code className="mono">{c.flag_key}</code>,
      },
      {
        key: "bu",
        header: "BU",
        render: (c) => <Chip tone="accent">{c.business_unit}</Chip>,
      },
      {
        key: "change",
        header: "Change",
        render: (c) => `prod → ${c.desired_enabled ? "On" : "Off"}`,
      },
      { key: "by", header: "Requested by", render: (c) => c.requested_by },
      {
        key: "age",
        header: "Age",
        render: (c) => (
          <span title={formatDateTime(c.created_at)}>
            {ageLabel(c.created_at)}
          </span>
        ),
      },
      {
        key: "status",
        header: "Status",
        render: (c) => <StatusBadge status={c.status} tone="warning" />,
      },
      {
        key: "actions",
        header: "",
        align: "right",
        render: (c) => {
          if (!canApprove) return null;
          const own = c.requested_by === me?.subject;
          return (
            <span className="row-actions">
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={own || deciding !== null}
                title={
                  own
                    ? "You requested this change; another approver must decide it"
                    : undefined
                }
                onClick={() => void decide(c, "approve")}
              >
                Approve
              </button>
              <button
                type="button"
                className="btn btn-danger btn-sm"
                disabled={own || deciding !== null}
                onClick={() => void decide(c, "reject")}
              >
                Reject
              </button>
            </span>
          );
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [canApprove, me?.subject, deciding],
  );

  return (
    <>
      <PageHeader
        title="Feature flags"
        subtitle="dev and staging toggles apply immediately. prod toggles need a second person (an approver) to approve."
      />
      <Card title="Pending prod changes">
        {pending.error ? (
          <ErrorState
            error={pending.error}
            onRetry={() => void pending.refetch()}
          />
        ) : !pending.data ? (
          <SkeletonRows rows={2} />
        ) : (
          <DataTable
            rows={pending.data}
            columns={pendingColumns}
            rowKey={(c) => c.id}
            caption="Pending prod change requests"
            empty={
              <EmptyState
                title="No pending changes"
                description="Prod toggle requests show up here."
              />
            }
          />
        )}
      </Card>
      <Card title="Flags">
        {states.error ? (
          <ErrorState
            error={states.error}
            onRetry={() => void states.refetch()}
          />
        ) : !states.data ? (
          <SkeletonRows rows={6} />
        ) : (
          <DataTable
            rows={grid.rows}
            columns={columns}
            rowKey={(r) => r.key}
            caption="Feature flags by business unit and environment"
            empty={
              <EmptyState
                title="No flags"
                description="No flags in your business units."
              />
            }
          />
        )}
      </Card>
      {selected && (
        <HistoryCard state={selected} onClose={() => setSelected(null)} />
      )}
      <ConfirmDialog
        open={target !== null}
        title={
          target
            ? `${target.environment === "prod" ? "Request" : "Apply"}: turn ${target.flag_key} ${target.enabled ? "off" : "on"}`
            : ""
        }
        confirmLabel={
          target?.environment === "prod" ? "Request approval" : "Apply now"
        }
        tone={target?.environment === "prod" ? "danger" : "primary"}
        busy={toggle.submitting}
        onConfirm={() => void confirmToggle()}
        onCancel={() => setTarget(null)}
      >
        {target && (
          <p>
            {target.business_unit} · {target.environment} (version{" "}
            {target.version}).{" "}
            {target.environment === "prod"
              ? "This creates a change request. A different flag approver must approve it before it takes effect."
              : "This takes effect immediately and is recorded in the audit log."}
          </p>
        )}
      </ConfirmDialog>
    </>
  );
}

function HistoryCard({
  state,
  onClose,
}: {
  state: FlagState;
  onClose: () => void;
}) {
  const api = useApi();
  const audit = useQuery(
    (signal) => flagsApi(api).audit(state.id, signal),
    [api, state.id, state.version],
  );
  return (
    <Card
      title={`History: ${state.flag_key} · ${state.business_unit} · ${state.environment}`}
      actions={
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onClose}
        >
          Close
        </button>
      }
    >
      {audit.error ? (
        <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
      ) : !audit.data ? (
        <SkeletonRows rows={3} />
      ) : audit.data.length === 0 ? (
        <EmptyState
          title="No changes yet"
          description="This flag has not been toggled."
        />
      ) : (
        <AuditTimeline entries={audit.data} />
      )}
    </Card>
  );
}
