"""Where a club meets, as Google names it (022_club_ministries.sql).

The browser does the Google part (Places Autocomplete, with the owner's key restricted to our
origins): the API never calls Google and holds no key. What reaches here is what the browser
picked — `address`, `place_id`, `maps_url`, coordinates — or, without a key, a typed address
and perhaps a pasted Google Maps link. This module only validates and normalises that:

  * `maps_url` must be a Google Maps link (never an arbitrary URL we would then show as a
    link on a public page);
  * a `place_id` without a link gets the canonical one;
  * a pasted link carries its coordinates (`!3d<lat>!4d<lng>` — the place itself — or
    `@<lat>,<lng>` — the map's centre —, or `?q=<lat>,<lng>`): they fill `latitude/longitude`
    when the body brings none.
"""
import re
from urllib.parse import unquote, urlsplit

ADDRESS_MAX = 300
MAPS_URL_MAX = 2000
PLACE_ID_PATTERN = r"^[A-Za-z0-9_-]{8,255}$"

_GOOGLE_HOST = re.compile(r"^(?:www\.|maps\.)?google\.(?:com|[a-z]{2,3})(?:\.[a-z]{2})?$")
_SHORT_HOSTS = {"maps.app.goo.gl", "goo.gl"}

_PLACE_COORDS = re.compile(r"!3d(-?\d{1,2}(?:\.\d+)?)!4d(-?\d{1,3}(?:\.\d+)?)")
_CENTER_COORDS = re.compile(r"@(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)")
_QUERY_COORDS = re.compile(
    r"[?&](?:q|query|destination|ll|center)=(?:loc:)?(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)"
)

INVALID_MAPS_URL = "maps_url debe ser un enlace de Google Maps (https)"


def place_url(place_id: str) -> str:
    """The link Google documents for opening a place by its id."""
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def is_google_maps_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower()
    if host in _SHORT_HOSTS:
        return host == "maps.app.goo.gl" or parts.path.startswith("/maps")
    if not _GOOGLE_HOST.match(host):
        return False
    return host.startswith("maps.") or parts.path.startswith("/maps")


def clean_maps_url(value: str | None) -> str | None:
    """`None`/blank → None; a Google Maps link → itself (trimmed); anything else → ValueError."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > MAPS_URL_MAX or any(char.isspace() for char in value) or not is_google_maps_url(value):
        raise ValueError(INVALID_MAPS_URL)
    return value


def clean_address(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def _valid(lat: float, lng: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)


def coords_from_maps_url(url: str | None) -> tuple[float, float] | None:
    """(latitude, longitude) written in a Google Maps link, or None. The place's own pin
    (`!3d…!4d…`) wins over the map's centre (`@lat,lng`)."""
    if not url:
        return None
    text = unquote(url)
    for pattern in (_PLACE_COORDS, _QUERY_COORDS, _CENTER_COORDS):
        match = pattern.search(text)
        if match:
            lat, lng = float(match.group(1)), float(match.group(2))
            if _valid(lat, lng):
                return round(lat, 7), round(lng, 7)
    return None
