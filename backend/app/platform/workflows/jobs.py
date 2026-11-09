"""Registry of scheduled jobs that YAML `schedules` can refer to by name.

A job finds the entities a schedule applies to and returns one `ScheduledHit` per entity and
window. The engine (not the job) turns hits into notifications via the schedule's declarative
actions, deduplicated with the outbox `dedupe_key`, so "notify once per window" survives
restarts and concurrent workers. Jobs run as `app_worker` inside the scheduler's transaction.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.platform.workflows.events import DomainEvent


@dataclass(frozen=True)
class ScheduledHit:
    event: DomainEvent
    # Unique per entity and escalation window, e.g. "<refund id>:w1".
    window_key: str
    # Called once, in the same transaction, when this hit produced new outbox messages.
    on_first_notify: Callable[[Session], None] | None = None


JobFn = Callable[[Session, Mapping[str, Any]], list[ScheduledHit]]


@dataclass(frozen=True)
class ParamSpec:
    type: type
    default: Any
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class RegisteredJob:
    name: str
    fn: JobFn
    params: Mapping[str, ParamSpec] = field(default_factory=dict)


_REGISTRY: dict[str, RegisteredJob] = {}


def scheduled_job(name: str, *, params: Mapping[str, ParamSpec] | None = None):
    def decorator(fn: JobFn) -> JobFn:
        if name in _REGISTRY and _REGISTRY[name].fn is not fn:
            raise ValueError(f"scheduled job {name!r} registered twice")
        _REGISTRY[name] = RegisteredJob(name=name, fn=fn, params=dict(params or {}))
        return fn

    return decorator


def get_job(name: str) -> RegisteredJob | None:
    _load_builtin_jobs()
    return _REGISTRY.get(name)


def _load_builtin_jobs() -> None:
    from app.platform.workflows import builtin_jobs  # noqa: F401  (registers on import)
