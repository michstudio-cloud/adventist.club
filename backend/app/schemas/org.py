"""Organization tree schemas."""
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.models import Organization
from app.schemas.ministry import MINISTRY_SLUG_PATTERN, MinistryRef
from app.services import places

# 422 detail of a new CLUB that names no ministry (rule 3 of ESTADO.md: nothing guesses it).
CLUB_MINISTRY_REQUIRED = "club_ministry_required"

# Top to bottom. A node's parent must be exactly one level above it.
ORG_HIERARCHY = ("division", "union", "association", "zone", "church", "club", "unit")


class GeoPoint(BaseModel):
    """GeoJSON point, as the legacy API exchanged it: [longitude, latitude]."""

    type: Literal["Point"] = "Point"
    coordinates: list[float] = Field(min_length=2, max_length=2)


MinistrySlug = Annotated[str, Field(pattern=MINISTRY_SLUG_PATTERN)]


class _PlaceFields(BaseModel):
    """Where a club meets, as Google Places named it (022): the formatted `address`, the
    `place_id` and a Google Maps link. Without a Places key the browser sends a typed address
    and perhaps a pasted link; the link's coordinates fill `latitude/longitude` when the body
    brings none (`app/services/places.py`)."""

    address: str | None = Field(default=None, max_length=places.ADDRESS_MAX)
    place_id: str | None = Field(default=None, pattern=places.PLACE_ID_PATTERN)
    maps_url: str | None = Field(default=None, max_length=places.MAPS_URL_MAX)

    @field_validator("address")
    @classmethod
    def _clean_address(cls, value: str | None) -> str | None:
        return places.clean_address(value)

    @field_validator("maps_url")
    @classmethod
    def _google_maps_only(cls, value: str | None) -> str | None:
        return places.clean_maps_url(value)

    @model_validator(mode="after")
    def _link_and_point(self):
        if self.place_id and not self.maps_url:
            self.maps_url = places.place_url(self.place_id)
            self.model_fields_set.add("maps_url")
        has_point = "latitude" in type(self).model_fields
        if (
            has_point
            and self.maps_url
            and getattr(self, "latitude", None) is None
            and getattr(self, "longitude", None) is None
            and "latitude" not in self.model_fields_set
            and "longitude" not in self.model_fields_set
        ):
            point = places.coords_from_maps_url(self.maps_url)
            if point is not None:
                self.latitude, self.longitude = point
                self.model_fields_set.update({"latitude", "longitude"})
        return self


class _OrgWritable(_PlaceFields):
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


class _MinistryChoice(BaseModel):
    """The ministries of a club (022: it may have several). `ministries: [slug…]` names them
    all, the first being the principal; `ministry` (slug) or `ministry_id` alone is still a
    list of one. With both, the single one must be in the list and becomes the principal.
    The service checks every slug against the database (`ministries.resolve_choice`)."""

    ministries: list[MinistrySlug] | None = Field(default=None, max_length=12)
    ministry: str | None = Field(default=None, pattern=MINISTRY_SLUG_PATTERN)
    ministry_id: uuid.UUID | None = None

    @field_validator("ministries")
    @classmethod
    def _at_least_one(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unique = list(dict.fromkeys(value))
        if not unique:
            # Minimum one: a club never goes back to «no ministry».
            raise ValueError(CLUB_MINISTRY_REQUIRED)
        return unique

    @property
    def names_a_ministry(self) -> bool:
        return bool(self.ministry) or self.ministry_id is not None or bool(self.ministries)


class OrgNodeCreate(_OrgWritable, _MinistryChoice):
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

    @model_validator(mode="after")
    def _club_names_its_ministry(self):
        if self.type == "club" and not self.names_a_ministry:
            raise ValueError(CLUB_MINISTRY_REQUIRED)
        if self.type != "club" and self.names_a_ministry:
            raise ValueError("Sólo un club tiene ministerio")
        return self


class OrgNodeUpdate(_OrgWritable, _MinistryChoice):
    """`ministry` / `ministry_id` change the ministry of a CLUB: only the administration of
    its association or above (never its director), and never back to «none»."""

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


class ClubSignup(_MinistryChoice, _PlaceFields):
    """A director's request to open a club under an association. Used both by
    `POST /auth/register` (field `club`) and `POST /org-nodes/clubs`.

    Decision D3: the director declares their CHURCH — an existing one of the
    association (`church_id`) or the name of a new one (`church_name`) — and
    never the zone. Zones are the association's to draw, and it assigns one
    when it accepts the request.
    """

    # `ministry` / `ministry_id` (inherited): REQUIRED since the registration screens ask
    # for it (422 `club_ministry_required`, checked by the service against the database).
    # Requests older than that may still lack it: whoever approves them assigns it.
    name: str = Field(min_length=2, max_length=180)
    association_id: uuid.UUID
    church_id: uuid.UUID | None = None
    church_name: str | None = Field(default=None, max_length=180)
    # Pre-E6 free-text field, accepted one more cycle as an alias of church_name.
    church: str | None = Field(default=None, max_length=180)
    city: str | None = Field(default=None, max_length=120)
    # Filled from Google Places (022) when the director picks the address; optional.
    state: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
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

    @model_validator(mode="after")
    def _exactly_one_church(self):
        if self.church_name is None and self.church is not None:
            self.church_name = self.church
        if bool(self.church_id) == bool(self.church_name):
            raise ValueError(
                "Indica la iglesia del club: elige una existente (church_id)"
                " o escribe su nombre (church_name)"
            )
        return self

    @field_validator("name", "church", "church_name", "city", "state", "country", "contact")
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


class _ChurchChoice(BaseModel):
    """One of the two ways to name a church: pick the row that exists, or
    propose a name. Never both, never neither."""

    church_id: uuid.UUID | None = None
    church_name: str | None = Field(default=None, max_length=180)

    model_config = {"extra": "forbid"}

    @field_validator("church_name")
    @classmethod
    def _trimmed_church(cls, value: str | None) -> str | None:
        value = " ".join((value or "").split())
        return value or None

    @model_validator(mode="after")
    def _exactly_one_church(self):
        if bool(self.church_id) == bool(self.church_name):
            raise ValueError("Indica `church_id` o `church_name`, exactamente uno")
        return self


class PlacementProposal(_ChurchChoice):
    """What the director declares about an existing unplaced club: their
    church, never the zone (decision D3)."""


class ClubPlacement(_ChurchChoice):
    """What the association resolves: the zone it assigns and the church the
    club hangs from. Creating a zone or a church by name is reserved to the
    administration of the association (or above)."""

    zone_id: uuid.UUID | None = None
    zone_name: str | None = Field(default=None, max_length=180)
    city: str | None = Field(default=None, max_length=120)

    @field_validator("zone_name", "city")
    @classmethod
    def _trimmed_zone(cls, value: str | None) -> str | None:
        value = " ".join((value or "").split())
        return value or None

    @model_validator(mode="after")
    def _exactly_one_zone(self):
        if bool(self.zone_id) == bool(self.zone_name):
            raise ValueError("Indica `zone_id` o `zone_name`, exactamente uno")
        return self


class ClubApproval(_MinistryChoice):
    """Optional body of `POST /org-nodes/{id}/approve`: whoever approves may
    correct what the director declared (club name, city, church) and assigns
    the zone. An already placed club needs none of it.

    `ministry` / `ministry_id`: REQUIRED when the request has none (requests from
    before the registration asked for it); otherwise optional, and a different one
    corrects it — only the administration of the association or above does that."""

    club_name: str | None = Field(default=None, min_length=2, max_length=180)
    city: str | None = Field(default=None, max_length=120)
    church_id: uuid.UUID | None = None
    church_name: str | None = Field(default=None, max_length=180)
    zone_id: uuid.UUID | None = None
    zone_name: str | None = Field(default=None, max_length=180)

    model_config = {"extra": "forbid"}

    @field_validator("club_name", "city", "church_name", "zone_name")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        value = " ".join((value or "").split())
        return value or None

    @model_validator(mode="after")
    def _at_most_one_of_each(self):
        if self.church_id and self.church_name:
            raise ValueError("Indica `church_id` o `church_name`, no los dos")
        if self.zone_id and self.zone_name:
            raise ValueError("Indica `zone_id` o `zone_name`, no los dos")
        return self

    @property
    def names_a_placement(self) -> bool:
        return any((self.church_id, self.church_name, self.zone_id, self.zone_name))


CLUB_LIST_STATUSES = ("active", "pending", "rejected", "inactive", "all")


class AdminClubCreate(_MinistryChoice, _PlaceFields):
    """`POST /org-nodes/clubs/admin`: the administration opens a club itself,
    already ACTIVE and connected to its association.

    The placement is optional and follows the rules of `ClubPlacement`: zone
    and church each by id OR by name, never both. A zone or a church typed by
    name is created only when both are given (a church never hangs straight
    off an association); a church alone that already has a zone places the
    club under it, and otherwise it stays declared for later, as a director's
    request would.

    The ministry (`ministry` slug or `ministry_id`) is REQUIRED: 422 without it.
    """

    name: str = Field(min_length=2, max_length=180)
    code: str | None = Field(default=None, max_length=60)
    association_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    zone_name: str | None = Field(default=None, max_length=180)
    church_id: uuid.UUID | None = None
    church_name: str | None = Field(default=None, max_length=180)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    director_email: EmailStr | None = None

    model_config = {"extra": "forbid"}

    @field_validator("name", "code", "zone_name", "church_name", "city", "state", "country")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        value = " ".join((value or "").split())
        return value or None

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str | None) -> str:
        if not value or len(value) < 2:
            raise ValueError("name must have at least 2 characters")
        return value

    @model_validator(mode="after")
    def _consistent(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude go together")
        if self.zone_id and self.zone_name:
            raise ValueError("Indica `zone_id` o `zone_name`, no los dos")
        if self.church_id and self.church_name:
            raise ValueError("Indica `church_id` o `church_name`, no los dos")
        if not self.names_a_ministry:
            raise ValueError(CLUB_MINISTRY_REQUIRED)
        return self


class ChurchPlacement(BaseModel):
    """`POST /org-nodes/churches/{id}/place`: the association moves a church —
    with its clubs — from one of its zones to another."""

    zone_id: uuid.UUID

    model_config = {"extra": "forbid"}


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
    # Only a CLUB has one (019_club_ministry.sql); None for every other node and for a
    # club that never declared one. Filled by `app.services.ministries.refs_for`.
    # Since 022 it is the PRINCIPAL ministry; `ministries` lists them all, principal first.
    ministry: MinistryRef | None = None
    ministries: list[MinistryRef] = []
    # 022: the meeting place as Google Places named it, and the club's logo.
    address: str | None = None
    place_id: str | None = None
    maps_url: str | None = None
    logo_url: str | None = None

    @classmethod
    def from_model(
        cls,
        node: Organization,
        ministry: MinistryRef | None = None,
        ministries: list[MinistryRef] | None = None,
    ) -> "OrgNodeResponse":
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
            ministry=ministry,
            ministries=ministries if ministries is not None else ([ministry] if ministry else []),
            address=node.address,
            place_id=node.place_id,
            maps_url=node.maps_url,
            logo_url=node.logo_url,
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
    """A club request as coordinators see it.

    `association`, `zone` and `church` come from the ANCESTORS of the club, not
    from its parent: since E6 a club may hang from its church, and a club that
    is not placed yet still hangs straight off the association (spec §5.5).
    `declared` is what the director asked for and nobody has resolved yet.
    """

    association: OrgRef | None = None
    zone: OrgRef | None = None
    church: OrgRef | None = None
    declared: dict[str, Any] | None = None
    requested_by: ClubRequester | None = None
    # Whoever the administration appointed when it created the club itself
    # (`POST /org-nodes/clubs/admin`). A request carries `requested_by` instead.
    director: ClubRequester | None = None

    @classmethod
    def build(
        cls,
        node: Organization,
        association: Organization | None,
        requester=None,
        *,
        zone: Organization | None = None,
        church: Organization | None = None,
        declared: dict | None = None,
        director=None,
        ministry: MinistryRef | None = None,
        ministries: list[MinistryRef] | None = None,
    ) -> "PendingClubResponse":
        base = OrgNodeResponse.from_model(node, ministry, ministries).model_dump()
        return cls(
            **base,
            association=_ref(association),
            zone=_ref(zone),
            church=_ref(church),
            declared=declared or None,
            requested_by=person_ref(requester),
            director=person_ref(director),
        )


def person_ref(user) -> ClubRequester | None:
    return ClubRequester(id=str(user.id), name=user.name, email=user.email) if user else None


class AdminClubRow(BaseModel):
    """One row of `GET /org-nodes/clubs/admin`. `association`, `zone` and
    `church` come from the ANCESTORS of the club, like every E6 read."""

    id: str
    name: str
    code: str | None = None
    status: str
    city: str | None = None
    state: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    association: OrgRef | None = None
    zone: OrgRef | None = None
    church: OrgRef | None = None
    director: ClubRequester | None = None
    members_count: int = 0
    ministry: MinistryRef | None = None
    ministries: list[MinistryRef] = []
    address: str | None = None
    place_id: str | None = None
    maps_url: str | None = None
    logo_url: str | None = None
    created_at: datetime
    updated_at: datetime


def _ref(node: Organization | None) -> OrgRef | None:
    return OrgRef(id=str(node.id), name=node.name, code=node.code) if node is not None else None


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


class ClubLocation(_PlaceFields):
    """`PUT /org-nodes/clubs/{id}/location`: where the club meets. The point (both or none)
    and, since 022, the address Google Places named (or a typed one and a pasted link, whose
    coordinates fill the point), plus the city / state / country Places filled in."""

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)

    model_config = {"extra": "forbid"}

    @field_validator("city", "state", "country")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        value = " ".join((value or "").split())
        return value or None

    @model_validator(mode="after")
    def _something_and_both(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude go together")
        if not self.model_fields_set:
            raise ValueError("Indica la ubicación o la dirección del club")
        return self


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
    # The NAME of the church: the node's when the club is placed, the
    # declaration while it is not. Kept as a string so pre-E6 clients keep
    # working; `church_ref` is the node itself when there is one.
    church: str | None = None
    church_ref: OrgRef | None = None
    zone: OrgRef | None = None
    # Whether the club is taking join requests today (spec E §5.2): `/clubs`
    # shows «Solicitar unirme» or «No recibe solicitudes» from this.
    accepts_requests: bool = True
    association: OrgRef | None = None
    ministry: MinistryRef | None = None
    ministries: list[MinistryRef] = []
    address: str | None = None
    maps_url: str | None = None
    logo_url: str | None = None


class UnplacedClub(BaseModel):
    """An active club that still hangs straight off its association, with the
    church its director declared, if any. Nothing about it is blocked: E6 is
    not destructive (spec §5.5)."""

    id: str
    name: str
    city: str | None = None
    association: OrgRef | None = None
    declared: dict[str, Any] | None = None
    created_at: datetime
