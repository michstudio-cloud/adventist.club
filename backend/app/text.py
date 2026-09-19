"""Small text helpers shared by the routers."""
import re
import unicodedata


def slugify(value: str, fallback: str = "item") -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower() or fallback


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so user input is matched literally (escape char: backslash)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
