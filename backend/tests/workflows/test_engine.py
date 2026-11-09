import pytest

from app.platform.notifications.formatting import format_money
from app.platform.workflows.config import load_config, parse_config
from app.platform.workflows.engine import evaluate
from app.platform.workflows.templates import compile_template
from tests.workflows.conftest import refund_event

from .test_config import SHIPPED


@pytest.fixture(scope="module")
def shipped():
    return load_config(SHIPPED)


@pytest.mark.parametrize(
    ("amount", "required", "auto", "rules"),
    [
        (1, 0, True, ["refund-small-auto-approve"]),
        (5000, 0, True, ["refund-small-auto-approve"]),
        (5001, None, False, []),
        (50000, None, False, []),
        (50001, 2, False, ["refund-large-two-approvals"]),
    ],
)
def test_shipped_refund_thresholds(shipped, amount, required, auto, rules):
    result = evaluate(shipped, refund_event(amount_cents=amount))
    assert result.required_approvals == required
    assert result.auto_approve is auto
    assert list(result.matched_rules) == rules


def test_large_refund_notifies_finance(shipped):
    result = evaluate(shipped, refund_event(amount_cents=250_000))
    assert [a.channel for _, a in result.notifications] == ["finance-approvals"]


def test_failed_and_succeeded_notify(shipped):
    failed = evaluate(shipped, refund_event("refund.failed", status="failed"))
    succeeded = evaluate(shipped, refund_event("refund.succeeded", status="succeeded"))
    assert [a.channel for _, a in failed.notifications] == ["payments-oncall"]
    assert [a.channel for _, a in succeeded.notifications] == ["refunds-activity"]
    assert failed.required_approvals is None and failed.auto_approve is False


COMBO = """
version: 1
rules:
  - {id: auto, on: refund.created, actions: [{auto_approve: {}}]}
  - {id: three, on: refund.created, conditions: [{field: business_unit, op: eq, value: EU}],
     actions: [{set_required_approvals: {count: 3}}]}
  - {id: one, on: refund.created, conditions: [{field: business_unit, op: in, value: [EU, US]}],
     actions: [{set_required_approvals: {count: 1}}]}
"""


def test_strictest_wins_and_overrides_auto_approve():
    cfg = parse_config(COMBO)
    eu = evaluate(cfg, refund_event(business_unit="EU"))
    assert (eu.required_approvals, eu.auto_approve) == (3, False)
    assert list(eu.matched_rules) == ["auto", "three", "one"]
    us = evaluate(cfg, refund_event(business_unit="US"))
    assert (us.required_approvals, us.auto_approve) == (1, False)
    other = evaluate(cfg, refund_event(business_unit="APAC"))
    assert (other.required_approvals, other.auto_approve) == (0, True)


def test_deterministic():
    cfg = parse_config(COMBO)
    event = refund_event(business_unit="EU")
    assert len({evaluate(cfg, event) for _ in range(20)}) == 1


def test_no_type_coercion_and_missing_fields_do_not_match():
    cfg = parse_config(
        "version: 1\nrules:\n"
        "  - {id: lte, on: refund.created, conditions: [{field: amount_cents, op: lte,"
        " value: 5000}], actions: [{auto_approve: {}}]}\n"
        "  - {id: ne, on: refund.created, conditions: [{field: missing, op: ne, value: x}],"
        " actions: [{auto_approve: {}}]}\n"
    )
    for amount in ("100", True, None, 10.0):
        assert evaluate(cfg, refund_event(amount_cents=amount)).matched_rules == ()


def test_other_events_ignored(shipped):
    result = evaluate(shipped, refund_event("refund.approved", amount_cents=1))
    assert result.matched_rules == () and result.required_approvals is None


def test_template_renders_only_plain_fields():
    t = compile_template("{name} owes {{literal}} {amount} {missing}")
    assert t.render({"name": "x", "amount": 5, "obj": object()}) == "x owes {literal} 5 -"
    # Values are never re-interpreted as templates or formatted with attributes.
    assert compile_template("{v}").render({"v": "{0.__class__}"}) == "{0.__class__}"
    assert compile_template("{v}").render({"v": object()}) == "-"


@pytest.mark.parametrize(
    ("cents", "currency", "expected"),
    [
        (250_000, "USD", "$2,500.00 USD"),
        (5, "EUR", "€0.05 EUR"),
        (123_456_789, "GBP", "£1,234,567.89 GBP"),
        (500, "JPY", "500 JPY"),
        (100, "CHF", "1.00 CHF"),
    ],
)
def test_format_money(cents, currency, expected):
    assert format_money(cents, currency) == expected
