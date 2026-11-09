from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DomainEvent:
    name: str  # e.g. "refund.created"
    entity_type: str  # e.g. "refund"
    entity_id: str
    actor: str
    data: dict[str, Any] = field(default_factory=dict)
