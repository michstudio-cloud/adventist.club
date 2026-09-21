"""Club units (Bloque E, E5).

Privacy note (spec §7): a unit is a named group of minors, so nothing here ever
carries a birth date or an e-mail. The roster line that mentions a unit only
carries its id and its name.

The frontend offers the Pathfinder brackets (10–11, 12–13, 14–15, 16+), but
numbers are what gets stored: the CORE is multi-ministry and Adventurers will
use 6–9.
"""
import uuid as uuid_module

from pydantic import BaseModel, Field, model_validator

MIN_AGE = 4
MAX_AGE = 120


class UnitRef(BaseModel):
    """A unit as it appears on somebody else's row."""

    id: str
    name: str


class PersonRef(BaseModel):
    id: str
    name: str


class _UnitWritable(BaseModel):
    min_age: int | None = Field(default=None, ge=MIN_AGE, le=MAX_AGE)
    max_age: int | None = Field(default=None, ge=MIN_AGE, le=MAX_AGE)
    capacity: int | None = Field(default=None, ge=1, le=50)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _ordered_bracket(self):
        if self.min_age is not None and self.max_age is not None and self.min_age > self.max_age:
            raise ValueError("min_age must not be greater than max_age")
        return self


class UnitCreate(_UnitWritable):
    name: str = Field(min_length=2, max_length=80)


class UnitUpdate(_UnitWritable):
    """Every field optional; `exclude_unset` tells "leave it" from "clear it"."""

    name: str | None = Field(default=None, min_length=2, max_length=80)


class UnitOut(BaseModel):
    id: str
    club_id: str
    name: str
    min_age: int | None = None
    max_age: int | None = None
    capacity: int | None = None
    # How many ACTIVE memberships are in it right now.
    members: int = 0
    counselor: PersonRef | None = None
    status: str


class CounselorAssign(BaseModel):
    """`null` takes the post away. The person is named by their MEMBERSHIP, so a
    unit can only ever be led by somebody who belongs to the same club."""

    membership_id: uuid_module.UUID | None = None

    model_config = {"extra": "forbid"}


class MemberUnitAssign(BaseModel):
    unit_id: uuid_module.UUID | None = None

    model_config = {"extra": "forbid"}


class MemberUnitOut(BaseModel):
    membership_id: str
    unit_id: str | None = None
    unit: UnitRef | None = None
    # True when the member's age falls outside the unit's bracket: the club is
    # told, and decides. It is never a refusal (spec §5.4).
    age_warning: bool = False


__all__ = [
    "CounselorAssign",
    "MemberUnitAssign",
    "MemberUnitOut",
    "PersonRef",
    "UnitCreate",
    "UnitOut",
    "UnitRef",
    "UnitUpdate",
]
