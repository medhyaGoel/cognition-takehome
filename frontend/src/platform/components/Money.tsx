import { formatCents } from "../money";

export function Money({
  cents,
  currency,
  compact,
  className,
}: {
  cents: number;
  currency: string;
  compact?: boolean;
  className?: string;
}) {
  return (
    <span className={`money ${className ?? ""}`} title={formatCents(cents, currency)}>
      {formatCents(cents, currency, { compact })}
    </span>
  );
}
