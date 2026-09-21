"""Which stored language serves a request. Shared by the catalogue (routers/honors.py)
and the portfolio so both fall back the same way. The language is always explicit
(`?locale=`), never taken from Accept-Language."""

SOURCE_LOCALE = "es"  # honors.name / honor_categories.name are written in Spanish
LOCALE_PATTERN = r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$"


def match_locale(stored: list[str], requested: str | None) -> str | None:
    """The stored locale that serves `requested`: exact, then the bare language, then any
    region of that language (pt -> pt-BR). None when the text does not exist in that language."""
    if not requested:
        return None
    by_lower = {value.lower(): value for value in sorted(stored)}
    language = requested.lower().split("-")[0]
    regional = next((v for k, v in by_lower.items() if k.startswith(f"{language}-")), None)
    return by_lower.get(requested.lower()) or by_lower.get(language) or regional


def best_locale(stored: list[str], requested: str | None) -> str:
    """Among the locales a text exists in: the requested one (exact, language, any region), then the
    source language, then English, then whatever there is."""
    by_lower = {value.lower(): value for value in sorted(stored)}
    return (
        match_locale(stored, requested)
        or by_lower.get(SOURCE_LOCALE)
        or by_lower.get("en")
        or next(iter(by_lower.values()), SOURCE_LOCALE)
    )
