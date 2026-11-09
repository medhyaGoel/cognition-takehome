import { asList } from "../../../platform/api/client";
import { useApi } from "../../../platform/api/context";
import { Card, PageHeader } from "../../../platform/components/AppShell";
import { Banner, EmptyState, ErrorState, SkeletonRows } from "../../../platform/components/States";
import { useQuery } from "../../../platform/hooks/useQuery";
import { refundsApi } from "../api";

type Rule = Record<string, unknown>;

const TITLE_KEYS = ["id", "name"];

function ruleTitle(rule: Rule, i: number): string {
  for (const k of TITLE_KEYS) if (typeof rule[k] === "string") return rule[k];
  return `Rule ${i + 1}`;
}

export function WorkflowRulesPage() {
  const api = useApi();
  const q = useQuery((s) => refundsApi(api).workflowRules(s), [api]);
  const rules = q.data === undefined ? undefined : asList<Rule>(q.data);

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
      ) : !rules ? (
        <Card>
          <SkeletonRows rows={3} />
        </Card>
      ) : rules.length === 0 ? (
        <Card>
          <EmptyState title="No rules loaded" description="Defaults apply: every refund needs 1 approval." />
        </Card>
      ) : (
        <div className="stack">
          {rules.map((rule, i) => {
            const title = ruleTitle(rule, i);
            const desc = typeof rule.description === "string" ? rule.description : null;
            const rest = Object.fromEntries(
              Object.entries(rule).filter(([k]) => !TITLE_KEYS.includes(k) && k !== "description"),
            );
            return (
              <Card key={`${title}-${i}`} title={<code className="mono">{title}</code>}>
                {desc && <p>{desc}</p>}
                <pre className="code-block">{JSON.stringify(rest, null, 2)}</pre>
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
}
