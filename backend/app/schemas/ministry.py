"""Ministries (Conquistadores, Aventureros, Guías Mayores) as the API names them."""
import uuid

from pydantic import BaseModel, Field

# The shape of a slug in `ministries.slug` (e.g. `pathfinders`, `master-guides`).
MINISTRY_SLUG_PATTERN = r"^[a-z0-9-]{2,60}$"


class MinistryRef(BaseModel):
    """The ministry of a club, as every club response nests it."""

    id: str
    slug: str
    name: str



# ----------------------------------------------------------------------------
# 024: the ministry and club the signed-in person works in (the shell's selector)
# ----------------------------------------------------------------------------
class ClubContext(BaseModel):
    """A club the person may pick in the selector: their active membership's club and the club
    their account is attached to (a director's), each once. `role`: the club role they hold."""

    id: str
    name: str
    role: str | None = None
    ministries: list[MinistryRef] = []


class MinistryContext(BaseModel):
    """What `GET /auth/me` and `GET /users/me` add for the person themself.

    * `ministries_available`: the ministries of their clubs plus Conquistadores (the public
      default); MASTER_GC and the administration (zone and above): every active ministry.
    * `active_ministry`: the slug in use — their saved choice while it is still available;
      otherwise the principal ministry of the active club; otherwise `pathfinders`.
    * `clubs_available` / `active_club_id`: the club switcher (a preference, never a permission).
    """

    ministries_available: list[MinistryRef] = []
    active_ministry: str | None = None
    clubs_available: list[ClubContext] = []
    active_club_id: str | None = None


class PreferencesUpdate(BaseModel):
    """`PATCH /users/me/preferences`. Only the fields present change; `null` = back to the
    default. An unknown or unavailable ministry / club is 422."""

    ministry: str | None = Field(default=None, pattern=MINISTRY_SLUG_PATTERN)
    club_id: uuid.UUID | None = None

    model_config = {"extra": "forbid"}
