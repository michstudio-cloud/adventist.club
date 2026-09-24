"""«Avisos» — the in-app inbox (017_notifications.sql)."""
from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: str
    kind: str
    # Spanish fallback; the app paints `kind` + `data` in the reader's language.
    title: str
    body: str | None
    # Always a path inside the app (`/portfolio/...`), or null.
    link: str | None
    entity_type: str | None
    entity_id: str | None
    # How many events this notice sums up (it grows while it stays unread).
    count: int
    data: dict
    read_at: datetime | None
    created_at: datetime


class UnreadCount(BaseModel):
    count: int


class ReadAllOut(BaseModel):
    updated: int
