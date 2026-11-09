import pytest

from app.platform.connectors.payment_provider import (
    FakePaymentProvider,
    IdempotencyKeyReuse,
    PaymentProvider,
    get_provider,
)


def _call(p, key="k1", amount=1_000):
    return p.refund(idempotency_key=key, payment_ref="ch_1", amount_cents=amount, currency="USD")


def test_idempotent_by_key():
    p = FakePaymentProvider()
    first, again = _call(p), _call(p)
    assert first.succeeded and first.provider_refund_id.startswith("re_")
    assert again == first
    assert p.charge_count == 1 and p.calls == {"k1": 2}
    assert _call(p, key="k2").provider_refund_id != first.provider_refund_id
    assert p.charge_count == 2


def test_key_reuse_with_different_params_raises():
    p = FakePaymentProvider()
    _call(p)
    with pytest.raises(IdempotencyKeyReuse):
        _call(p, amount=999)
    assert p.charge_count == 1


def test_deterministic_failure_trigger():
    p = FakePaymentProvider()
    result = _call(p, amount=1_013)
    assert not result.succeeded and result.failure_reason == "declined_by_provider"
    assert result.provider_refund_id is None and p.charge_count == 0
    assert _call(p, amount=1_013) == result


def test_get_provider_is_a_singleton():
    provider: PaymentProvider = get_provider()
    assert provider is get_provider()
