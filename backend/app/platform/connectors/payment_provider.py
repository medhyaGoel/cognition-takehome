"""Payment provider connector (Stripe/Adyen/... adapter boundary).

Apps depend only on the `PaymentProvider` protocol. Every call carries an idempotency key
(the refund id): calling again with the same key must return the original result and never
move money twice. That property is what makes the worker's crash recovery safe
(`app.apps.refunds.execution.reconcile_stuck_processing` simply re-calls the provider).

A provider *declining* a refund is a definitive result (`ProviderRefundResult.succeeded is
False`). A raised exception means the outcome is unknown (timeout, crash): callers must leave
the refund in `processing` and reconcile later, never assume failure.

`FakePaymentProvider` (local dev, demos, tests):
  * idempotent by key: the same key returns the same `provider_refund_id`; money moves once
    (`charge_count`). Reusing a key with a different amount/currency/payment raises
    `IdempotencyKeyReuse` (as real providers do).
  * deterministic failure trigger: any refund whose `amount_cents` ends in **13** (e.g. 1013 =
    10.13) is declined with reason `"declined_by_provider"`.
  * state is in memory (per process); a real provider persists idempotency keys server-side.
"""

import hashlib
import threading
from dataclasses import dataclass
from typing import Protocol

FAILURE_TRIGGER_CENTS = 13
FAILURE_REASON = "declined_by_provider"


@dataclass(frozen=True)
class ProviderRefundResult:
    succeeded: bool
    provider_refund_id: str | None = None
    failure_reason: str | None = None


class IdempotencyKeyReuse(Exception):
    """The same idempotency key was used with different parameters."""


class PaymentProvider(Protocol):
    def refund(
        self, *, idempotency_key: str, payment_ref: str, amount_cents: int, currency: str
    ) -> ProviderRefundResult: ...


class FakePaymentProvider:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: dict[str, tuple[tuple[str, int, str], ProviderRefundResult]] = {}
        self.calls: dict[str, int] = {}
        self.charge_count = 0

    def refund(
        self, *, idempotency_key: str, payment_ref: str, amount_cents: int, currency: str
    ) -> ProviderRefundResult:
        params = (payment_ref, amount_cents, currency)
        with self._lock:
            self.calls[idempotency_key] = self.calls.get(idempotency_key, 0) + 1
            existing = self._results.get(idempotency_key)
            if existing is not None:
                if existing[0] != params:
                    raise IdempotencyKeyReuse(idempotency_key)
                return existing[1]
            if amount_cents % 100 == FAILURE_TRIGGER_CENTS:
                result = ProviderRefundResult(succeeded=False, failure_reason=FAILURE_REASON)
            else:
                digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]
                result = ProviderRefundResult(succeeded=True, provider_refund_id=f"re_{digest}")
                self.charge_count += 1
            self._results[idempotency_key] = (params, result)
            return result


_provider: PaymentProvider = FakePaymentProvider()


def get_provider() -> PaymentProvider:
    return _provider
