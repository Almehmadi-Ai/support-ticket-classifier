from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Category(str, Enum):
    billing = "billing"
    technical = "technical"
    account = "account"
    other = "other"


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class TicketStatus(str, Enum):
    pending = "pending"
    classified = "classified"
    failed = "failed"


MAX_SUMMARY_LENGTH = 300


class ClassificationResult(BaseModel):
    """The only shape allowed to become a stored classification.

    Invalid categories, priorities, or an empty summary raise a ValidationError,
    which the classifier turns into a retry rather than persisting.
    """

    category: Category
    priority: Priority
    summary: str

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("summary must not be empty")
        if len(cleaned) > MAX_SUMMARY_LENGTH:
            raise ValueError("summary is too long")
        return cleaned


class TicketCreate(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=10_000)


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subject: str
    body: str
    status: TicketStatus
    category: Category | None
    priority: Priority | None
    summary: str | None
    classification_attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class TicketList(BaseModel):
    items: list[TicketResponse]
    total: int
    limit: int
    offset: int
