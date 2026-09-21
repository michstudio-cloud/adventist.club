"""Organization tree schemas."""
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import Organization

# Top to bottom. A node's parent must be exactly one level above it.
ORG_HIERARCHY = ("division", "union", "association", "zone", "church", "club", "unit")


class GeoPoint(BaseModel):
    """GeoJSON point, as the legacy API exchanged it: [longitude, latitude]."""

    type: Literal["Point"] = "Point"
    coordinates: list[float] = Field(min_length=2, max_length=2)


class _OrgWritable(BaseModel):
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    location: GeoPoint | None = None
    metadata: dict[str, Any] | None = None
    code: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _location_to_lat_lng(self):
        if self.location is not None:
            self.longitude, self.latitude = self.location.coordinates
            if not (-90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
                raise ValueError("location.coordinates must be [longitude, latitude]")
            self.model_fields_set.update({"latitude", "longitude"})
        return self


class OrgNodeCreate(_OrgWritable):
    name: str = Field(min_length=1, max_length=180)
    type: str
    parent_id: uuid.UUID | None = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ORG_HIERARCHY:
            raise ValueError(f"type must be one of {[t.upper() for t in ORG_HIERARCHY]}")
        return value


class OrgNodeUpdate(_OrgWritable):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    status: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value not in {"active", "inactive"}:
            raise ValueError("status must be ACTIVE or INACTIVE")
        return value


class ClubSignup(BaseModel):
    """A director's request to open a club under an association. Used both by
    `POST /auth/register` (field `club`) and `POST /org-nodes/clubs`."""

    name: str = Field(min_length=2, max_length=180)
    association_id: uuid.UUID
    church: str | None = Field(default=None, max_length=180)
    city: str | None = Field(default=None, max_length=120)
    contact: str | None = Field(default=None, max_length=180)
    # Where the physical club meets, so people can find it by location.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _both_coordinates(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude go together")
        return self

    @field_validator("name", "church", "city", "contact")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str | None) -> str:
        if not value or len(value) < 2:
            raise ValueError("name must have at least 2 characters")
        return value


class ClubDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class OrgSearchResult(BaseModel):
    """Compact row for pickers: the node plus the name of its parent."""

    id: str
    name: str
    type: str
    code: str | None
    parent_id: str | None
    parent_name: str | None
    country: str | None


class OrgNodeResponse(BaseModel):
    id: str
    name: str
    type: str
    code: str | None
    parent_id: str | None
    path: str | None
    path_ids: list[str]
    city: str | None
    state: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    location: GeoPoint | None
    metadata: dict[str, Any] | None
    status: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, node: Organization) -> "OrgNodeResponse":
        location = None
        if node.latitude is not None and node.longitude is not None:
            location = GeoPoint(coordinates=[node.longitude, node.latitude])
        return cls(
            id=str(node.id),
            name=node.name,
            # Stored lowercase; the legacy API (and its clients) speak uppercase.
            type=node.type.upper(),
            code=node.code,
            parent_id=str(node.parent_id) if node.parent_id else None,
            path=node.path,
            path_ids=_ancestor_ids(node.path),
            city=node.city,
            state=node.state,
            country=node.country,
            latitude=node.latitude,
            longitude=node.longitude,
            location=location,
            metadata=node.metadata_json,
            status=node.status.upper(),
            created_at=node.created_at,
            updated_at=node.updated_at,
        )


class OrgRef(BaseModel):
    id: str
    name: str
    code: str | None = None


class ClubRequester(BaseModel):
    id: str
    name: str
    email: str


class PendingClubResponse(OrgNodeResponse):
    """A club request as coordinators see it."""

    association: OrgRef | None = None
    requested_by: ClubRequester | None = None

    @classmethod
    def build(
        cls, node: Organization, association: Organization | None, requester=None
    ) -> "PendingClubResponse":
        base = OrgNodeResponse.from_model(node).model_dump()
        return cls(
            **base,
            association=(
                OrgRef(id=str(association.id), name=association.name, code=association.code)
                if association is not None
                else None
            ),
            requested_by=(
                ClubRequester(id=str(requester.id), name=requester.name, email=requester.email)
                if requester is not None
                else None
            ),
        )


def _ancestor_ids(path: str | None) -> list[str]:
    """Ancestor ids, root first (the legacy `path_ids`). Labels are uuid hex."""
    if not path:
        return []
    ancestors = []
    for label in path.split(".")[:-1]:
        try:
            ancestors.append(str(uuid.UUID(hex=label)))
        except ValueError:
            ancestors.append(label)
    return ancestors


class ClubLocation(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    model_config = {"extra": "forbid"}


class NearbyClub(BaseModel):
    """A club found by location. Coordinates are those of the meeting place the director pinned."""

    id: str
    name: str
    distance_km: float
    latitude: float
    longitude: float
    city: str | None = None
    state: str | None = None
    country: str | None = None
    church: str | None = None
    association: OrgRef | None = None
