import pathlib

import pytest

from app.config import settings
from app.platform.workflows.config import WorkflowConfigError, load_config, parse_config

SHIPPED = pathlib.Path(settings.workflows_path)

BASE = """
version: 1
channels:
  ops: {adapters: [in_app, log]}
rules:
"""


def rule(body: str) -> str:
    return BASE + body


def test_shipped_file_is_valid():
    cfg = load_config(SHIPPED)
    assert [r.id for r in cfg.rules] == [
        "refund-small-auto-approve",
        "refund-large-two-approvals",
        "refund-failed-page-oncall",
        "refund-succeeded-activity",
    ]
    (sla,) = cfg.schedules
    assert sla.job == "refund_sla_escalation" and sla.params["sla_minutes"] == 60
    assert cfg.channels["finance-approvals"].email_to == ("finance@example.com",)


def test_on_key_is_not_parsed_as_boolean():
    cfg = parse_config(rule("  - {id: r1, 'on': a.b, actions: [{auto_approve: {}}]}\n"))
    cfg2 = parse_config(rule("  - id: r1\n    on: a.b\n    actions: [{auto_approve: {}}]\n"))
    assert cfg.rules[0].on == cfg2.rules[0].on == "a.b"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("  - {id: r1, on: a.b, conditions: [{field: x, op: regex, value: 1}],"
         " actions: [{auto_approve: {}}]}", "unknown op 'regex'"),
        ("  - {id: r1, on: a.b, actions: [{delete_everything: {}}]}", "unknown action"),
        ("  - {id: r1, on: a.b, actions: [{auto_approve: {}}]}\n"
         "  - {id: r1, on: a.b, actions: [{auto_approve: {}}]}", "duplicate id(s): r1"),
        ("  - {id: r1, id: r2, on: a.b, actions: [{auto_approve: {}}]}", "duplicate key 'id'"),
        ("  - {id: r1, on: a.b, actions: [{notify: {channel: nope, template: x}}]}",
         "unknown channel 'nope'"),
        ("  - {id: r1, on: a.b, actions: [{notify: {channel: ops, template: '{x.__class__}'}}]}",
         "no attribute access"),
        ("  - {id: r1, on: a.b, actions: [{notify: {channel: ops, template: '{x[0]}'}}]}",
         "no attribute access"),
        ("  - {id: r1, on: a.b, actions: [{notify: {channel: ops, template: '{}'}}]}",
         "positional"),
        ("  - {id: r1, on: a.b, actions: [{notify: {channel: ops, template: '{x:>9999}'}}]}",
         "format specs are not allowed"),
        ("  - {id: r1, on: a.b, actions: [{notify: {channel: ops, template: '{x!r}'}}]}",
         "conversions are not allowed"),
        ("  - {id: r1, on: a.b, conditions: [{field: x, op: gt, value: '5'}],"
         " actions: [{auto_approve: {}}]}", "must be an integer"),
        ("  - {id: r1, on: a.b, conditions: [{field: x, op: eq, value: 1.5}],"
         " actions: [{auto_approve: {}}]}", "floats are not allowed"),
        ("  - {id: r1, on: a.b, actions: [{set_required_approvals: {count: 9}}]}",
         "between 0 and 5"),
        ("  - {id: r1, on: a.b, extra: 1, actions: [{auto_approve: {}}]}", "unknown key(s): extra"),
        ("  - {id: r1, on: a.b, actions: []}", "must not be empty"),
        ("  - {id: r1, on: 'x; import os', actions: [{auto_approve: {}}]}", "invalid value"),
        ("  - {id: r1, on: a.b, conditions: [{field: __class__.x, op: eq, value: 1}],"
         " actions: [{auto_approve: {}}]}", "invalid value"),
    ],
)
def test_invalid_rules_fail_fast_with_clear_error(body, message):
    with pytest.raises(WorkflowConfigError) as exc:
        parse_config(rule(body))
    assert message in str(exc.value)


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ("version: 2\n", "only version 1"),
        ("rules: []\n", "missing required key(s): version"),
        ("version: 1\nchannels: {c: {adapters: [log]}}\n", "must include in_app"),
        ("version: 1\nchannels: {c: {adapters: [in_app, sms]}}\n", "unknown adapter 'sms'"),
        ("version: 1\nchannels: {c: {adapters: [in_app, email]}}\n", "email_to"),
        ("version: 1\nchannels: {c: {adapters: [in_app, email], email_to: [nope]}}\n",
         "invalid value"),
        ("version: 1\nlinks: {refund: 'https://evil.test/{id}'}\n", "invalid value"),
        ("version: 1\nschedules: [{id: s, job: nope, actions: [{auto_approve: {}}]}]\n",
         "unknown job 'nope'"),
        ("version: 1\nchannels: {c: {adapters: [in_app]}}\nschedules: [{id: s,"
         " job: refund_sla_escalation, actions: [{auto_approve: {}}]}]\n",
         "only support notify"),
        ("version: 1\nchannels: {c: {adapters: [in_app]}}\nschedules: [{id: s,"
         " job: refund_sla_escalation, params: {sla_minutes: 0},"
         " actions: [{notify: {channel: c, template: x}}]}]\n", "sla_minutes"),
        ("version: 1\nrules: [\n", "invalid YAML"),
        ("version: 1\nx: !!python/object/apply:os.system [echo hi]\n", "invalid YAML"),
    ],
)
def test_invalid_documents(doc, message):
    with pytest.raises(WorkflowConfigError) as exc:
        parse_config(doc)
    assert message in str(exc.value)


def test_sla_minutes_defaults_to_60():
    cfg = parse_config(
        "version: 1\nchannels: {c: {adapters: [in_app]}}\nschedules: [{id: s,"
        " job: refund_sla_escalation, actions: [{notify: {channel: c, template: x}}]}]\n"
    )
    assert cfg.schedules[0].params["sla_minutes"] == 60


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(WorkflowConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml")
