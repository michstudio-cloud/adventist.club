"""Bloque F · F2 — Actividades: horas de servicio y asistencia a reuniones."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.program import ActivityCategory
from app.schemas.portfolio import PersonRef

NOTE_MAX_LENGTH = 500
# The director registers an outing of the whole club in one go, never a census.
MAX_MEMBERS_PER_LOG = 60


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


class ActivityCreate(BaseModel):
    category: ActivityCategory
    performed_on: date
    # Hours (at most 24 in one day) or meetings. Halves are allowed: 1.5 h.
    quantity: float = Field(gt=0, le=24)
    description: str = Field(min_length=3, max_length=NOTE_MAX_LENGTH)
    place: str | None = Field(default=None, max_length=180)
    # Only a director (or an administrator) sends this: the members the activity is for.
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_MEMBERS_PER_LOG)

    _clean = field_validator("place", mode="before")(_blank_to_none)

    @field_validator("performed_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("La fecha de la actividad no puede estar en el futuro.")
        return value

    @field_validator("quantity")
    @classmethod
    def _half_hours_at_most(cls, value: float) -> float:
        if round(value * 10) != value * 10:
            raise ValueError("La cantidad se registra con un decimal como mucho.")
        return round(value, 1)


class ActivityDecision(BaseModel):
    status: Literal["APPROVED", "REJECTED"]
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)

    _clean = field_validator("note", mode="before")(_blank_to_none)

    @model_validator(mode="after")
    def _rejecting_needs_a_note(self):
        if self.status == "REJECTED" and not self.note:
            raise ValueError("Un rechazo necesita una nota para el miembro.")
        return self


class ActivityOut(BaseModel):
    id: str
    user: PersonRef
    category: ActivityCategory
    performed_on: date
    quantity: float
    description: str
    place: str | None
    status: str
    decided_by: PersonRef | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime
    # True when the reader may decide this one right now (the club's «Horas por aprobar»).
    can_decide: bool = False


class ActivityListOut(BaseModel):
    logs: list[ActivityOut]
    # Approved quantity by category, for the bar of a `HOURS` requirement.
    totals: dict[str, float]
