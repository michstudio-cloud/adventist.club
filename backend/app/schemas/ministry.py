"""Ministries (Conquistadores, Aventureros, Guías Mayores) as the API names them."""
from pydantic import BaseModel

# The shape of a slug in `ministries.slug` (e.g. `pathfinders`, `master-guides`).
MINISTRY_SLUG_PATTERN = r"^[a-z0-9-]{2,60}$"


class MinistryRef(BaseModel):
    """The ministry of a club, as every club response nests it."""

    id: str
    slug: str
    name: str

