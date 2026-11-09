import { useApi } from "../../../platform/api/context";
import { Card, PageHeader } from "../../../platform/components/AppShell";
import { Chip } from "../../../platform/components/StatusBadge";
import { Banner, EmptyState, ErrorState, SkeletonRows } from "../../../platform/components/States";
import { useQuery } from "../../../platform/hooks/useQuery";
import { formatDuration } from "../../../platform/time";
import { refundsApi } from "../api";
import type { RuleAction, RuleCondition, WorkflowRule } from "../types";

const OPS: Record<string, string> = { eq: "=", ne: "≠", gt: ">", gte: "≥", lt: "<", lte: "≤", in: "in" };

function conditionText(c: RuleCondition): string {
  const value = typeof c.value === "string" ? c.value : JSON.stringify(c.value);
  return `${c.field} ${OPS[c.op] ?? c.op} ${value}`;
}

function ActionItem({ action }: { action: RuleAction }) {
  switch (action.type) {
    case "set_required_approvals":
      return (
        <li>
          {action.count === 0
            ? "Require no approvals"
            : `Require ${action.count} approval${action.count === 1 ? "" : "s"}`}
        </li>
      );
    case "auto_approve":
      return <li>Auto-approve</li>;
    case "notify":
      return (
        <li>
          Notify <code className="mono">#{action.channel}</code> via {action.adapters.join(", ")}
          {action.title && <div className="muted small mono">{action.title}</div>}
          <div className="muted small mono">{action.template}</div>
        </li>
      );
    default:
      return <li className="mono">{JSON.stringify(action)}</li>;
  }
}

function RuleCard({ rule }: { rule: WorkflowRule }) {
  return (
    <Card
      title={
        <span className="rule-title">
          <code className="mono">{rule.id}</code>
          <Chip tone={rule.kind === "schedule" ? "info" : "accent"}>{rule.kind}</Chip>
        </span>
      }
    >
      <p>{rule.description}</p>
      <dl className="kv">
        <dt>When</dt>
        <dd>
          {rule.kind === "schedule" && rule.schedule ? (
            <>
              Every {formatDuration(rule.schedule.every_seconds * 1000)} · job{" "}
              <code className="mono">{rule.schedule.job}</code>
            </>
          ) : (
            <code className="mono">{rule.on}</code>
          )}
          {rule.conditions.map((c) => (
            <span key={`${c.field}-${c.op}`}>
              {" "}
              and <code className="mono">{conditionText(c)}</code>
            </span>
          ))}
        </dd>
        {rule.schedule && Object.keys(rule.schedule.params).length > 0 && (
          <>
            <dt>Params</dt>
            <dd className="mono small">{JSON.stringify(rule.schedule.params)}</dd>
          </>
        )}
        <dt>Then</dt>
        <dd>
          <ul className="rule-actions">
            {rule.actions.map((a, i) => (
              <ActionItem key={i} action={a} />
            ))}
          </ul>
        </dd>
      </dl>
    </Card>
  );
}

export function WorkflowRulesPage() {
  const api = useApi();
  const q = useQuery((s) => refundsApi(api).workflowRules(s), [api]);

  return (
    <>
      <PageHeader title="Workflow rules" subtitle="Declarative approval and notification rules currently loaded." />
      <Banner tone="info">
        Read-only. Rules change through a pull request to <code>config/workflows.yaml</code>, so every change is
        code-reviewed and versioned.
      </Banner>
      {q.error ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        </Card>
      ) : !q.data ? (
        <Card>
          <SkeletonRows rows={3} />
        </Card>
      ) : q.data.length === 0 ? (
        <Card>
          <EmptyState title="No rules loaded" description="Defaults apply: every refund needs 1 approval." />
        </Card>
      ) : (
        <div className="stack">
          {q.data.map((rule) => (
            <RuleCard key={rule.id} rule={rule} />
          ))}
        </div>
      )}
    </>
  );
}
