import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Environment = Literal["dev", "staging", "prod"]
ChangeStatus = Literal["applied", "pending", "approved", "rejected"]


class FlagStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    flag_key: str
    description: str
    business_unit: str
    environment: Environment
    enabled: bool
    version: int
    updated_at: datetime
    updated_by: str | None


class ToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    expected_version: int = Field(ge=1)


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=500)


class ChangeRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    flag_state_id: uuid.UUID
    flag_key: str
    business_unit: str
    environment: Environment
    desired_enabled: bool
    expected_version: int
    status: ChangeStatus
    requested_by: str
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    version: int
    created_at: datetime
