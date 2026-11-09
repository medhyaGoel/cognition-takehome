"""Load and validate `config/workflows.yaml` (declarative rules; reviewed like code).

Validation is strict and fails fast with a path to the offending entry: unknown keys, unknown
ops/actions/channels/adapters/jobs, duplicate ids, duplicate YAML keys, wrongly typed values
and unsafe templates are all errors. Nothing in the file is ever evaluated as code.

    version: 1
    channels:
      <name>: {description?, adapters: [in_app, webhook|log|email, ...], recipient_role?,
               email_to?: [addresses] (required with email), email_subject_prefix?}
    links:                       # optional: entity_type -> web path ({id} = entity id)
      refund: /refunds/{id}
    rules:
      - id, description?, on: <event name>,
        conditions?: [{field, op: eq|ne|gt|gte|lt|lte|in, value}]   # AND
        actions: [ {set_required_approvals: {count}} | {auto_approve: {}} |
                   {notify: {channel, template, title?}} ]
    schedules:
      - id, description?, job: <registered job>, every_seconds?, params?,
        actions: [ {notify: {...}} ]
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.platform.auth import ALL_ROLES
from app.platform.workflows.jobs import get_job
from app.platform.workflows.templates import Template, TemplateError, compile_template

OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "in")
NUMERIC_OPS = frozenset({"gt", "gte", "lt", "lte"})
ADAPTERS = frozenset({"in_app", "log", "webhook", "email"})
MAX_REQUIRED_APPROVALS = 5  # matches the refunds.required_approvals CHECK
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_EVENT = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_FIELD = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_LINK = re.compile(r"^/[A-Za-z0-9/_.-]*(\{id\}[A-Za-z0-9/_.-]*)?$")


class WorkflowConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class SetRequiredApprovals:
    count: int


@dataclass(frozen=True)
class AutoApprove:
    pass


@dataclass(frozen=True)
class Notify:
    channel: str
    template: Template
    title: Template | None = None


Action = SetRequiredApprovals | AutoApprove | Notify


@dataclass(frozen=True)
class Channel:
    name: str
    adapters: tuple[str, ...]
    recipient_role: str | None = None
    description: str = ""
    email_to: tuple[str, ...] = ()
    email_subject_prefix: str = ""


@dataclass(frozen=True)
class Rule:
    id: str
    on: str
    conditions: tuple[Condition, ...]
    actions: tuple[Action, ...]
    description: str = ""


@dataclass(frozen=True)
class Schedule:
    id: str
    job: str
    every_seconds: int
    params: Mapping[str, Any]
    actions: tuple[Notify, ...]
    description: str = ""


@dataclass(frozen=True)
class WorkflowConfig:
    channels: Mapping[str, Channel] = field(default_factory=dict)
    rules: tuple[Rule, ...] = ()
    schedules: tuple[Schedule, ...] = ()
    links: Mapping[str, str] = field(default_factory=dict)  # entity_type -> "/path/{id}"


# --- YAML loading ------------------------------------------------------------------------------


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys and only treats true/false as booleans
    (YAML 1.1 would turn `on:` into True and `value: no` into False)."""


_StrictLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf")
)


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False):
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise WorkflowConfigError(
                f"duplicate key {key!r} (line {key_node.start_mark.line + 1})"
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_config(path: str | Path) -> WorkflowConfig:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowConfigError(f"{p}: cannot read workflows file: {exc}") from exc
    return parse_config(text, source=str(p))


def parse_config(text: str, *, source: str = "<workflows>") -> WorkflowConfig:
    try:
        raw = yaml.load(text, Loader=_StrictLoader)  # noqa: S506  (SafeLoader subclass)
    except WorkflowConfigError as exc:
        raise WorkflowConfigError(f"{source}: {exc}") from None
    except yaml.YAMLError as exc:
        raise WorkflowConfigError(f"{source}: invalid YAML: {exc}") from None
    try:
        return _parse(raw)
    except WorkflowConfigError as exc:
        raise WorkflowConfigError(f"{source}: {exc}") from None


# --- validation --------------------------------------------------------------------------------


def _fail(path: str, msg: str) -> None:
    raise WorkflowConfigError(f"{path}: {msg}")


def _mapping(value: Any, path: str, *, required: set[str], optional: set[str]) -> dict:
    if not isinstance(value, dict):
        _fail(path, "must be a mapping")
    keys = set(value)
    if missing := required - keys:
        _fail(path, f"missing required key(s): {', '.join(sorted(missing))}")
    if unknown := keys - required - optional:
        _fail(path, f"unknown key(s): {', '.join(sorted(map(str, unknown)))}")
    return value


def _list(value: Any, path: str, *, allow_empty: bool = True) -> list:
    if not isinstance(value, list):
        _fail(path, "must be a list")
    if not allow_empty and not value:
        _fail(path, "must not be empty")
    return value


def _str(value: Any, path: str, pattern: re.Pattern | None = None) -> str:
    if not isinstance(value, str):
        _fail(path, "must be a string")
    if pattern is not None and not pattern.match(value):
        _fail(path, f"invalid value {value!r}")
    return value


def _int(value: Any, path: str, lo: int | None = None, hi: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(path, "must be an integer")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        _fail(path, f"must be between {lo} and {hi}")
    return value


def _scalar(value: Any, path: str) -> Any:
    if not isinstance(value, bool | int | str):
        _fail(path, "must be an integer, string or boolean (floats are not allowed)")
    return value


def _parse(raw: Any) -> WorkflowConfig:
    doc = _mapping(
        raw, "$", required={"version"}, optional={"channels", "rules", "schedules", "links"}
    )
    if doc["version"] != 1:
        _fail("version", "only version 1 is supported")
    channels = _parse_channels(doc.get("channels") or {})
    rules = tuple(
        _parse_rule(r, f"rules[{i}]", channels)
        for i, r in enumerate(_list(doc.get("rules") or [], "rules"))
    )
    schedules = tuple(
        _parse_schedule(s, f"schedules[{i}]", channels)
        for i, s in enumerate(_list(doc.get("schedules") or [], "schedules"))
    )
    ids = [r.id for r in rules] + [s.id for s in schedules]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        _fail("rules/schedules", f"duplicate id(s): {', '.join(dupes)}")
    links = doc.get("links") or {}
    if not isinstance(links, dict):
        _fail("links", "must be a mapping of entity_type -> path")
    for entity_type, path in links.items():
        _str(entity_type, f"links.{entity_type}", _FIELD)
        _str(path, f"links.{entity_type}", _LINK)
    return WorkflowConfig(channels=channels, rules=rules, schedules=schedules, links=links)


def _parse_channels(raw: Any) -> dict[str, Channel]:
    if not isinstance(raw, dict):
        _fail("channels", "must be a mapping of channel name -> settings")
    channels: dict[str, Channel] = {}
    for name, spec in raw.items():
        path = f"channels.{name}"
        _str(name, path, _ID)
        spec = _mapping(
            spec,
            path,
            required={"adapters"},
            optional={"description", "recipient_role", "email_to", "email_subject_prefix"},
        )
        adapters = _list(spec["adapters"], f"{path}.adapters", allow_empty=False)
        for j, a in enumerate(adapters):
            if a not in ADAPTERS:
                _fail(f"{path}.adapters[{j}]", f"unknown adapter {a!r} (use {sorted(ADAPTERS)})")
        if len(set(adapters)) != len(adapters):
            _fail(f"{path}.adapters", "duplicate adapter")
        if "in_app" not in adapters:
            _fail(f"{path}.adapters", "must include in_app (every notification is shown in-app)")
        role = spec.get("recipient_role")
        if role is not None and role not in ALL_ROLES:
            _fail(f"{path}.recipient_role", f"unknown role {role!r}")
        email_to = _list(spec.get("email_to") or [], f"{path}.email_to")
        for j, addr in enumerate(email_to):
            _str(addr, f"{path}.email_to[{j}]", _EMAIL)
        if ("email" in adapters) != bool(email_to):
            _fail(f"{path}.email_to", "is required with (and only with) the email adapter")
        prefix = _str(spec.get("email_subject_prefix", ""), f"{path}.email_subject_prefix")
        if len(prefix) > 60 or "\n" in prefix or "\r" in prefix:
            _fail(f"{path}.email_subject_prefix", "must be a single line of at most 60 characters")
        channels[name] = Channel(
            name=name,
            adapters=tuple(adapters),
            recipient_role=role,
            description=_str(spec.get("description", ""), f"{path}.description"),
            email_to=tuple(email_to),
            email_subject_prefix=prefix,
        )
    return channels


def _parse_condition(raw: Any, path: str) -> Condition:
    c = _mapping(raw, path, required={"field", "op", "value"}, optional=set())
    field_ = _str(c["field"], f"{path}.field", _FIELD)
    op = c["op"]
    if op not in OPS:
        _fail(f"{path}.op", f"unknown op {op!r} (use one of {', '.join(OPS)})")
    value = c["value"]
    if op in NUMERIC_OPS:
        _int(value, f"{path}.value")
    elif op == "in":
        items = _list(value, f"{path}.value", allow_empty=False)
        value = tuple(_scalar(v, f"{path}.value[{k}]") for k, v in enumerate(items))
    else:
        _scalar(value, f"{path}.value")
    return Condition(field=field_, op=op, value=value)


def _parse_notify(raw: Any, path: str, channels: Mapping[str, Channel]) -> Notify:
    n = _mapping(raw, path, required={"channel", "template"}, optional={"title"})
    channel = n["channel"]
    if channel not in channels:
        _fail(f"{path}.channel", f"unknown channel {channel!r} (declare it under channels)")
    try:
        template = compile_template(n["template"])
        title = compile_template(n["title"]) if "title" in n else None
    except TemplateError as exc:
        raise WorkflowConfigError(f"{path}: {exc}") from None
    return Notify(channel=channel, template=template, title=title)


def _parse_action(raw: Any, path: str, channels: Mapping[str, Channel]) -> Action:
    if not isinstance(raw, dict) or len(raw) != 1:
        _fail(path, "an action is a mapping with exactly one key (the action name)")
    ((name, args),) = raw.items()
    if name == "set_required_approvals":
        a = _mapping(args, f"{path}.{name}", required={"count"}, optional=set())
        return SetRequiredApprovals(
            count=_int(a["count"], f"{path}.{name}.count", 0, MAX_REQUIRED_APPROVALS)
        )
    if name == "auto_approve":
        if args not in (None, {}):
            _fail(f"{path}.auto_approve", "takes no arguments (use `auto_approve: {}`)")
        return AutoApprove()
    if name == "notify":
        return _parse_notify(args, f"{path}.notify", channels)
    _fail(path, f"unknown action {name!r} (use set_required_approvals, auto_approve, notify)")
    raise AssertionError("unreachable")


def _parse_rule(raw: Any, path: str, channels: Mapping[str, Channel]) -> Rule:
    r = _mapping(
        raw, path, required={"id", "on", "actions"}, optional={"description", "conditions"}
    )
    rule_id = _str(r["id"], f"{path}.id", _ID)
    path = f"{path}({rule_id})"
    conditions = tuple(
        _parse_condition(c, f"{path}.conditions[{i}]")
        for i, c in enumerate(_list(r.get("conditions") or [], f"{path}.conditions"))
    )
    actions = tuple(
        _parse_action(a, f"{path}.actions[{i}]", channels)
        for i, a in enumerate(_list(r["actions"], f"{path}.actions", allow_empty=False))
    )
    return Rule(
        id=rule_id,
        on=_str(r["on"], f"{path}.on", _EVENT),
        conditions=conditions,
        actions=actions,
        description=_str(r.get("description", ""), f"{path}.description"),
    )


def _parse_schedule(raw: Any, path: str, channels: Mapping[str, Channel]) -> Schedule:
    s = _mapping(
        raw,
        path,
        required={"id", "job", "actions"},
        optional={"description", "every_seconds", "params"},
    )
    schedule_id = _str(s["id"], f"{path}.id", _ID)
    path = f"{path}({schedule_id})"
    job_name = _str(s["job"], f"{path}.job")
    job = get_job(job_name)
    if job is None:
        _fail(f"{path}.job", f"unknown job {job_name!r}")
    raw_params = s.get("params") or {}
    if not isinstance(raw_params, dict):
        _fail(f"{path}.params", "must be a mapping")
    if unknown := set(raw_params) - set(job.params):
        _fail(f"{path}.params", f"unknown param(s) for {job_name}: {', '.join(sorted(unknown))}")
    params: dict[str, Any] = {}
    for pname, spec in job.params.items():
        value = raw_params.get(pname, spec.default)
        if spec.type is int:
            _int(value, f"{path}.params.{pname}", spec.minimum, spec.maximum)
        elif not isinstance(value, spec.type):
            _fail(f"{path}.params.{pname}", f"must be {spec.type.__name__}")
        params[pname] = value
    actions: list[Notify] = []
    for i, a in enumerate(_list(s["actions"], f"{path}.actions", allow_empty=False)):
        action = _parse_action(a, f"{path}.actions[{i}]", channels)
        if not isinstance(action, Notify):
            _fail(f"{path}.actions[{i}]", "schedules only support notify actions")
        actions.append(action)
    return Schedule(
        id=schedule_id,
        job=job_name,
        every_seconds=_int(s.get("every_seconds", 60), f"{path}.every_seconds", 5, 86_400),
        params=params,
        actions=tuple(actions),
        description=_str(s.get("description", ""), f"{path}.description"),
    )
