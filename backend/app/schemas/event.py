"""Eventos y puntajes (spec docs/superpowers/specs/2026-09-24-eventos.md §3).

Bodies only validate SHAPE; ranges of `config` and `inputs` are the scoring engine's job
(`app/services/event_scoring.py`), which is the one place that knows each `kind`.
"""
from __future__ import annotations

import uuid as uuid_module
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.ministry import MinistryRef

EventStatus = Literal["DRAFT", "OPEN", "IN_PROGRESS", "CLOSED", "ARCHIVED"]
ActivityKind = Literal["participation", "rubric", "bands", "per_correct", "stations", "group"]
ActivityStatus = Literal["READY", "TO_DEFINE"]
StaffRole = Literal["COORDINATOR", "JUDGE"]
AdjustmentKind = Literal["BONUS", "PENALTY"]
AmountMode = Literal["FIXED", "FREE"]
AMOUNT_MODE_RULE = "FIXED usa points (o ninguno: por definir); FREE usa max_points (o ninguno)"

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
KEY_PATTERN = r"^[A-Za-z0-9._:-]{8,100}$"
DATES_RULE = "La fecha de fin no puede ser anterior a la de inicio"


def _squeeze(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def _reason(value: str) -> str:
    value = " ".join((value or "").split())
    if not value:
        raise ValueError("El motivo es obligatorio")
    return value


class _Body(BaseModel):
    model_config = {"extra": "forbid"}


# ----------------------------------------------------------------------------
# Eventos
# ----------------------------------------------------------------------------
class EventCreate(_Body):
    organization_id: uuid_module.UUID
    ministry: str | None = Field(default=None, max_length=60)
    ministry_id: uuid_module.UUID | None = None
    name: str = Field(min_length=3, max_length=200)
    slug: str | None = Field(default=None, max_length=120, pattern=SLUG_PATTERN)
    venue: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    starts_on: date
    ends_on: date
    registration_closes_on: date | None = None
    honor_bands: list[dict[str, Any]] | None = None
    source_note: str | None = Field(default=None, max_length=4000)

    _clean = field_validator("name", "venue", "city", mode="before")(_squeeze)

    @model_validator(mode="after")
    def _dates(self):
        if self.ends_on < self.starts_on:
            raise ValueError(DATES_RULE)
        if self.ministry is None and self.ministry_id is None:
            raise ValueError("Falta el ministerio del evento")
        return self


class EventUpdate(_Body):
    organization_id: uuid_module.UUID | None = None
    ministry: str | None = Field(default=None, max_length=60)
    ministry_id: uuid_module.UUID | None = None
    name: str | None = Field(default=None, min_length=3, max_length=200)
    slug: str | None = Field(default=None, max_length=120, pattern=SLUG_PATTERN)
    venue: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    starts_on: date | None = None
    ends_on: date | None = None
    registration_closes_on: date | None = None
    honor_bands: list[dict[str, Any]] | None = None
    source_note: str | None = Field(default=None, max_length=4000)

    _clean = field_validator("name", "venue", "city", mode="before")(_squeeze)


class EventStatusChange(_Body):
    status: EventStatus
    reason: str | None = Field(default=None, max_length=500)


class EventDuplicate(_Body):
    name: str = Field(min_length=3, max_length=200)
    slug: str | None = Field(default=None, max_length=120, pattern=SLUG_PATTERN)
    starts_on: date
    ends_on: date
    organization_id: uuid_module.UUID | None = None

    _clean = field_validator("name", mode="before")(_squeeze)

    @model_validator(mode="after")
    def _dates(self):
        if self.ends_on < self.starts_on:
            raise ValueError(DATES_RULE)
        return self


class OrgBrief(BaseModel):
    id: str
    name: str
    code: str | None = None


class EventOut(BaseModel):
    id: str
    organization: OrgBrief
    ministry: MinistryRef | None
    name: str
    slug: str
    venue: str | None
    city: str | None
    starts_on: date
    ends_on: date
    status: EventStatus
    registration_closes_on: date | None
    rules_version: int
    honor_bands: list[dict[str, Any]]
    source_note: str | None
    template_of_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    # What the reader is in this event (ADMIN, COORDINATOR, JUDGE, DIRECTOR).
    my_roles: list[str] = []


# ----------------------------------------------------------------------------
# Actividades
# ----------------------------------------------------------------------------
class ActivityCreate(_Body):
    parent_id: uuid_module.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    kind: ActivityKind
    max_points: float | None = Field(default=None, ge=0, le=100000)
    config: dict[str, Any] = Field(default_factory=dict)
    status: ActivityStatus = "READY"
    counts_to_total: bool = True
    schedule_at: datetime | None = None
    position: int | None = Field(default=None, ge=0, le=10000)

    _clean = field_validator("name", mode="before")(_squeeze)


class ActivityUpdate(_Body):
    parent_id: uuid_module.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    kind: ActivityKind | None = None
    max_points: float | None = Field(default=None, ge=0, le=100000)
    config: dict[str, Any] | None = None
    status: ActivityStatus | None = None
    counts_to_total: bool | None = None
    schedule_at: datetime | None = None

    _clean = field_validator("name", mode="before")(_squeeze)


class ActivityReorder(_Body):
    parent_id: uuid_module.UUID | None = None
    ids: list[uuid_module.UUID] = Field(min_length=1, max_length=200)


class ActivityOut(BaseModel):
    id: str
    event_id: str
    parent_id: str | None
    position: int
    name: str
    description: str | None
    kind: ActivityKind
    max_points: float | None
    config: dict[str, Any]
    status: ActivityStatus
    counts_to_total: bool
    schedule_at: datetime | None
    # The config passes the strict validation (READY activities always do).
    config_complete: bool
    config_max: float | None


# ----------------------------------------------------------------------------
# Tipos de ajuste
# ----------------------------------------------------------------------------
class AdjustmentTypeCreate(_Body):
    kind: AdjustmentKind
    label: str = Field(min_length=1, max_length=200)
    amount_mode: AmountMode = "FIXED"
    # FIXED: the amount (None = to define). FREE: never; use max_points (optional bound).
    points: float | None = Field(default=None, gt=0, le=100000)
    max_points: float | None = Field(default=None, gt=0, le=100000)
    max_per_event: int | None = Field(default=None, ge=1, le=10000)
    max_per_club: int | None = Field(default=None, ge=1, le=10000)
    position: int | None = Field(default=None, ge=0, le=10000)

    _clean = field_validator("label", mode="before")(_squeeze)

    @model_validator(mode="after")
    def _mode(self):
        if (self.amount_mode == "FIXED" and self.max_points is not None) or (
            self.amount_mode == "FREE" and self.points is not None
        ):
            raise ValueError(AMOUNT_MODE_RULE)
        return self


class AdjustmentTypeUpdate(_Body):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    amount_mode: AmountMode | None = None
    points: float | None = Field(default=None, gt=0, le=100000)
    max_points: float | None = Field(default=None, gt=0, le=100000)
    max_per_event: int | None = Field(default=None, ge=1, le=10000)
    max_per_club: int | None = Field(default=None, ge=1, le=10000)
    position: int | None = Field(default=None, ge=0, le=10000)
    active: bool | None = None

    _clean = field_validator("label", mode="before")(_squeeze)


class AdjustmentTypeOut(BaseModel):
    id: str
    event_id: str
    kind: AdjustmentKind
    label: str
    amount_mode: AmountMode
    points: float | None
    max_points: float | None
    # FIXED without points: cannot be applied yet.
    to_define: bool
    max_per_event: int | None
    max_per_club: int | None
    position: int
    active: bool


# ----------------------------------------------------------------------------
# Personal
# ----------------------------------------------------------------------------
class StaffCreate(_Body):
    user_id: uuid_module.UUID | None = None
    email: str | None = Field(default=None, max_length=254)
    role: StaffRole
    activity_id: uuid_module.UUID | None = None

    @model_validator(mode="after")
    def _who(self):
        if (self.user_id is None) == (self.email is None):
            raise ValueError("Indica user_id o email (uno de los dos)")
        if self.role == "COORDINATOR" and self.activity_id is not None:
            raise ValueError("Sólo un juez se asigna a una actividad")
        return self


class StaffOut(BaseModel):
    id: str
    event_id: str
    user_id: str
    name: str
    email: str | None
    role: StaffRole
    activity_id: str | None
    active: bool
    created_at: datetime | None


# ----------------------------------------------------------------------------
# Inscripciones
# ----------------------------------------------------------------------------
class RegistrationCreate(_Body):
    club_id: uuid_module.UUID


class RegistrationUpdate(_Body):
    finalist_flags: dict[str, bool]


class ResolvePass(_Body):
    token: str = Field(min_length=8, max_length=200)


class ClubBrief(BaseModel):
    id: str
    name: str
    city: str | None = None


class RegistrationOut(BaseModel):
    id: str
    event_id: str
    club: ClubBrief
    status: Literal["REGISTERED", "WITHDRAWN"]
    has_pass: bool
    finalist_flags: dict[str, bool]
    created_at: datetime | None
    # Only right after creating or regenerating it; never stored in clear.
    pass_token: str | None = None


# ----------------------------------------------------------------------------
# Evaluaciones
# ----------------------------------------------------------------------------
class EvaluationCreate(_Body):
    registration_id: uuid_module.UUID
    activity_id: uuid_module.UUID
    inputs: dict[str, Any]
    idempotency_key: str = Field(pattern=KEY_PATTERN)
    # The rules the judge's screen was built with; a mismatch is 409 (reload).
    rules_version: int | None = Field(default=None, ge=1)


class EvaluationCorrect(_Body):
    inputs: dict[str, Any]
    reason: str = Field(max_length=500)
    expected_revision: int = Field(ge=1)
    idempotency_key: str | None = Field(default=None, pattern=KEY_PATTERN)

    _reason = field_validator("reason")(_reason)


class EvaluationVoid(_Body):
    reason: str = Field(max_length=500)
    expected_revision: int | None = Field(default=None, ge=1)

    _reason = field_validator("reason")(_reason)


class EvaluationOut(BaseModel):
    id: str
    registration_id: str
    activity_id: str
    inputs: dict[str, Any]
    points: float
    breakdown: dict[str, Any]
    rules_version: int
    status: Literal["CONFIRMED", "VOID"]
    judge_id: str | None
    # JUDGE, or COORDINATION when coordination entered it (judge_id is who did).
    captured_as: Literal["JUDGE", "COORDINATION"]
    revision: int
    idempotency_key: str
    created_at: datetime | None
    updated_at: datetime | None
    # False when the call replayed an earlier one (same idempotency_key).
    created: bool | None = None


class EvaluationRevisionOut(BaseModel):
    id: str
    revision: int
    inputs: dict[str, Any]
    points: float
    status: str
    rules_version: int
    judge_id: str | None
    action: str
    reason: str
    changed_by_id: str | None
    created_at: datetime | None


# ----------------------------------------------------------------------------
# Ajustes
# ----------------------------------------------------------------------------
class AdjustmentCreate(_Body):
    registration_id: uuid_module.UUID
    adjustment_type_id: uuid_module.UUID | None = None
    kind: AdjustmentKind | None = None
    points: float | None = Field(default=None, gt=0, le=100000)
    activity_id: uuid_module.UUID | None = None
    reason: str = Field(max_length=1000)

    _reason = field_validator("reason")(_reason)


class AdjustmentVoid(_Body):
    reason: str = Field(max_length=1000)

    _reason = field_validator("reason")(_reason)


class AdjustmentOut(BaseModel):
    id: str
    registration_id: str
    activity_id: str | None
    kind: AdjustmentKind
    adjustment_type_id: str | None
    label: str | None
    points: float
    reason: str
    status: Literal["PENDING", "APPROVED", "VOID"]
    created_by_id: str | None
    approved_by_id: str | None
    approved_at: datetime | None
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime | None
