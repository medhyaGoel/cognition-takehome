import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { useApi } from "../../../platform/api/context";
import { ApiError } from "../../../platform/api/errors";
import { Card, PageHeader } from "../../../platform/components/AppShell";
import { Money } from "../../../platform/components/Money";
import { Chip } from "../../../platform/components/StatusBadge";
import { Banner, EmptyState, Spinner, errorText } from "../../../platform/components/States";
import { useToast } from "../../../platform/components/Toast";
import { useIdempotentSubmit, type KeyFactory } from "../../../platform/hooks/useIdempotentSubmit";
import { centsToDecimalString, formatCents, parseAmountToCents } from "../../../platform/money";
import { formatDateTime } from "../../../platform/time";
import { refundsApi } from "../api";
import type { Payment, Refund } from "../types";

export function NewRefundPage({ newKey }: { newKey?: KeyFactory } = {}) {
  const api = useApi();
  const navigate = useNavigate();
  const toast = useToast();
  const ids = { q: useId(), amount: useId(), reason: useId() };

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Payment[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<ApiError | null>(null);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const r = refundsApi(api);
  const shownResults = query.trim().length >= 2 ? results : null;

  // Debounced payment search.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) return;
    const ctrl = new AbortController();
    const t = window.setTimeout(() => {
      setSearching(true);
      refundsApi(api)
        .searchPayments(q, ctrl.signal)
        .then((rows) => {
          setResults(rows);
          setSearchError(null);
        })
        .catch((err: unknown) => {
          if (!ctrl.signal.aborted && err instanceof ApiError) setSearchError(err);
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setSearching(false);
        });
    }, 250);
    return () => {
      ctrl.abort();
      window.clearTimeout(t);
    };
  }, [api, query]);

  const selectPayment = useCallback(
    async (id: string) => {
      try {
        // Always re-read: refundable_cents changes as other refunds reserve funds.
        setPayment(await refundsApi(api).getPayment(id));
      } catch (err) {
        if (err instanceof ApiError) toast.push("danger", errorText(err));
      }
    },
    [api, toast],
  );

  const parsed = parseAmountToCents(amount, payment?.currency ?? "USD");
  const amountError = !parsed.ok
    ? parsed.error
    : payment && parsed.cents > payment.refundable_cents
      ? `Exceeds refundable amount (${formatCents(payment.refundable_cents, payment.currency)})`
      : null;
  const reasonError = reason.trim().length === 0 ? "A reason is required" : null;
  const valid = payment !== null && !amountError && !reasonError;

  const send = useCallback(
    (key: string) => {
      if (!payment || !parsed.ok) throw new Error("invalid form");
      return r.createRefund(
        { payment_id: payment.id, amount_cents: parsed.cents, reason: reason.trim() },
        key,
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [api, payment, parsed.ok && parsed.cents, reason],
  );
  const { submit, submitting, retrying, reset } = useIdempotentSubmit(send, newKey);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!valid || submitting) return;
    setSubmitError(null);
    try {
      const res = await submit();
      if (!res) return;
      const refund: Refund = res.data;
      toast.push(
        "success",
        res.status === 200 ? "This refund was already submitted; showing it." : "Refund requested.",
      );
      navigate(`/refunds/${refund.id}`);
    } catch (err) {
      if (!(err instanceof ApiError)) {
        setSubmitError("Unexpected error");
        return;
      }
      if (err.outcomeUnknown) {
        setSubmitError(
          `${errorText(err)} Your request may or may not have been recorded. Submitting again is safe: it reuses the same idempotency key, so it can't create a duplicate refund.`,
        );
      } else if (err.code === "idempotency_mismatch") {
        reset();
        setSubmitError(
          "An earlier attempt of this submission was already recorded with different details. Check the queue before submitting again.",
        );
      } else if (err.code === "insufficient_refundable_amount") {
        setSubmitError("The refundable amount changed since you loaded this payment. It has been refreshed.");
        void selectPayment(payment!.id);
      } else {
        setSubmitError(errorText(err, "Payment not found or outside your business units"));
      }
    }
  };

  return (
    <>
      <PageHeader title="New refund" subtitle="Refunds reserve funds immediately and go through approval." />
      <div className="grid-2">
        <Card title="1. Find the payment">
          <label className="field">
            <span className="label" id={ids.q}>
              Search by provider reference or customer name
            </span>
            <div className="input-wrap">
              <input
                className="input"
                aria-labelledby={ids.q}
                value={query}
                placeholder="e.g. pi_1001 or Jane Doe"
                onChange={(e) => setQuery(e.target.value)}
                autoFocus
              />
              {searching && <Spinner label="Searching" />}
            </div>
          </label>
          {searchError && <Banner tone="danger">{errorText(searchError)}</Banner>}
          {shownResults && shownResults.length === 0 && (
            <EmptyState title="No payments found" description="Only payments in your business units are searchable." />
          )}
          {shownResults && shownResults.length > 0 && (
            <ul className="result-list" aria-label="Payments">
              {shownResults.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    className={`result ${payment?.id === p.id ? "selected" : ""}`}
                    onClick={() => void selectPayment(p.id)}
                  >
                    <span className="result-main">
                      <span className="mono">{p.provider_ref}</span>
                      <span>{p.customer_name}</span>
                    </span>
                    <span className="result-side">
                      <Money cents={p.amount_cents} currency={p.currency} />
                      <Chip tone="accent">{p.business_unit}</Chip>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="2. Refund details">
          {!payment ? (
            <EmptyState title="Select a payment" description="Search and pick the payment to refund." />
          ) : (
            <form onSubmit={(e) => void onSubmit(e)} noValidate>
              <dl className="kv">
                <dt>Payment</dt>
                <dd className="mono">{payment.provider_ref}</dd>
                <dt>Customer</dt>
                <dd>
                  {payment.customer_name} <span className="muted">{payment.customer_email}</span>
                </dd>
                <dt>Business unit</dt>
                <dd>
                  <Chip tone="accent">{payment.business_unit}</Chip>
                </dd>
                <dt>Captured</dt>
                <dd>{formatDateTime(payment.captured_at)}</dd>
                <dt>Charged</dt>
                <dd>
                  <Money cents={payment.amount_cents} currency={payment.currency} />
                </dd>
                <dt>Refundable</dt>
                <dd>
                  <strong data-testid="refundable">
                    <Money cents={payment.refundable_cents} currency={payment.currency} />
                  </strong>
                  {(payment.reserved_cents > 0 || payment.refunded_cents > 0) && (
                    <span className="muted small">
                      {" "}
                      ({formatCents(payment.reserved_cents, payment.currency)} reserved,{" "}
                      {formatCents(payment.refunded_cents, payment.currency)} refunded)
                    </span>
                  )}
                </dd>
              </dl>

              <label className="field">
                <span className="label">Amount ({payment.currency})</span>
                <div className="input-row">
                  <input
                    id={ids.amount}
                    className={`input ${touched && amountError ? "invalid" : ""}`}
                    inputMode="decimal"
                    autoComplete="off"
                    placeholder={centsToDecimalString(0, payment.currency)}
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    aria-invalid={touched && !!amountError}
                    aria-describedby={`${ids.amount}-err`}
                  />
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => setAmount(centsToDecimalString(payment.refundable_cents, payment.currency))}
                    disabled={payment.refundable_cents <= 0}
                  >
                    Full refundable
                  </button>
                </div>
                <span id={`${ids.amount}-err`} className="field-error">
                  {touched && amountError}
                </span>
              </label>

              <label className="field">
                <span className="label">Reason</span>
                <textarea
                  id={ids.reason}
                  className={`input ${touched && reasonError ? "invalid" : ""}`}
                  rows={3}
                  maxLength={1000}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  aria-invalid={touched && !!reasonError}
                />
                <span className="field-error">{touched && reasonError}</span>
              </label>

              {submitError && <Banner tone={retrying ? "warning" : "danger"}>{submitError}</Banner>}

              <div className="form-actions">
                <button type="submit" className="btn btn-primary" disabled={submitting}>
                  {submitting ? "Submitting…" : retrying ? "Retry submission" : "Request refund"}
                </button>
              </div>
            </form>
          )}
        </Card>
      </div>
    </>
  );
}
