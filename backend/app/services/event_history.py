"""«Cada punto tiene historia»: the feed of every change to the points of an event.

No table of its own: it is read from the three places that already keep it all —
`evaluations` (the capture), `evaluation_revisions` (the values BEFORE each correction or
void, with who and why) and `event_adjustments` (applied and voided bonuses/penalties).

Item types:
  EVALUATION_CREATED    evaluations.created_at; the points of revision 1
  EVALUATION_CORRECTED  one CORRECTION revision: previous_points = that revision's points,
                        points = the next revision's (or the current row's)
  EVALUATION_VOIDED     one VOID revision: points = what stopped counting
  ADJUSTMENT_APPLIED    approved_at (created_at while pending)
  ADJUSTMENT_VOIDED     voided_at

Who sees what (the router decides, `Scope` carries it):
  * coordination (COORDINATOR staff, admins in scope): everything, pending included;
  * a judge: the entries of the activities they judge (all of them when assigned to all);
  * a director: only their own club's registrations, never pending items.
Newest first; `before` is an opaque cursor (the last item's position). Item ids are stable:
`ev:<evaluation>` (capture), `rv:<revision>`, `aa:<adjustment>` (applied), `av:<adjustment>`.
"""
from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event
from app.schemas.event import (
    HistoryActivity,
    HistoryActor,
    HistoryItem,
    HistoryPage,
    HistoryRegistration,
)

MAX_LIMIT = 200
BAD_CURSOR = "Cursor de historial inválido"


@dataclass
class Scope:
    """None = no restriction on that axis. When both `activity_ids` and `registration_ids`
    are given, an item passes if EITHER admits it (a judge who also directs a club)."""
    all: bool = False
    activity_ids: set[uuid.UUID] | None = None
    registration_ids: set[uuid.UUID] | None = None
    include_pending: bool = False


def encode_cursor(at: datetime, item_id: str) -> str:
    raw = f"{at.isoformat()}|{item_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, item_id = base64.urlsafe_b64decode(padded.encode()).decode().split("|", 1)
        moment = datetime.fromisoformat(at)
        if moment.tzinfo is None or not item_id:
            raise ValueError
        return moment, item_id
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, BAD_CURSOR)


# Every item of the event, one row each. `pending` marks adjustments never approved.
_ITEMS = """
WITH regs AS (
  SELECT r.id, o.name AS club_name
  FROM event_registrations r JOIN organizations o ON o.id = r.club_id
  WHERE r.event_id = :event_id
),
items AS (
  SELECT 'ev:' || e.id AS item_id, e.created_at AS at, 'EVALUATION_CREATED' AS type,
         e.registration_id, e.activity_id, coalesce(r1.points, e.points) AS points,
         NULL::numeric AS previous_points, NULL::varchar AS kind,
         coalesce(r1.judge_id, e.judge_id) AS actor_id, e.captured_as AS actor_as,
         NULL::text AS reason, false AS pending
  FROM evaluations e
  JOIN regs ON regs.id = e.registration_id
  LEFT JOIN evaluation_revisions r1 ON r1.evaluation_id = e.id AND r1.revision = 1

  UNION ALL
  SELECT 'rv:' || rv.id, rv.created_at,
         CASE rv.action WHEN 'VOID' THEN 'EVALUATION_VOIDED' ELSE 'EVALUATION_CORRECTED' END,
         e.registration_id, e.activity_id,
         CASE rv.action WHEN 'VOID' THEN rv.points ELSE coalesce(nx.points, e.points) END,
         CASE rv.action WHEN 'VOID' THEN NULL ELSE rv.points END,
         NULL, rv.changed_by_id,
         CASE WHEN rv.action <> 'VOID' AND EXISTS (
                SELECT 1 FROM event_staff s JOIN event_activities a ON a.id = e.activity_id
                WHERE s.event_id = :event_id AND s.user_id = rv.changed_by_id AND s.role = 'JUDGE'
                  AND (s.activity_id IS NULL OR s.activity_id = a.id OR s.activity_id = a.parent_id))
              THEN 'JUDGE' ELSE 'COORDINATION' END,
         rv.reason, false
  FROM evaluation_revisions rv
  JOIN evaluations e ON e.id = rv.evaluation_id
  JOIN regs ON regs.id = e.registration_id
  LEFT JOIN evaluation_revisions nx ON nx.evaluation_id = rv.evaluation_id AND nx.revision = rv.revision + 1

  UNION ALL
  SELECT 'aa:' || j.id, coalesce(j.approved_at, j.created_at), 'ADJUSTMENT_APPLIED',
         j.registration_id, j.activity_id, j.points, NULL, j.kind,
         coalesce(j.approved_by_id, j.created_by_id), 'COORDINATION', j.reason,
         j.approved_by_id IS NULL
  FROM event_adjustments j JOIN regs ON regs.id = j.registration_id

  UNION ALL
  SELECT 'av:' || j.id, j.voided_at, 'ADJUSTMENT_VOIDED',
         j.registration_id, j.activity_id, j.points, NULL, j.kind,
         j.voided_by_id, 'COORDINATION', j.void_reason, j.approved_by_id IS NULL
  FROM event_adjustments j JOIN regs ON regs.id = j.registration_id
  WHERE j.voided_at IS NOT NULL
)
SELECT i.item_id, i.at, i.type, i.registration_id, regs.club_name, i.activity_id,
       a.name AS activity_name, i.points, i.previous_points, i.kind, u.name AS actor_name,
       i.actor_as, i.reason, i.pending
FROM items i
JOIN regs ON regs.id = i.registration_id
LEFT JOIN event_activities a ON a.id = i.activity_id
LEFT JOIN users u ON u.id = i.actor_id
WHERE {where}
ORDER BY i.at DESC, i.item_id DESC
LIMIT :limit
"""


async def page(db: AsyncSession, event: Event, scope: Scope, *, registration_id: uuid.UUID | None,
               limit: int, before: str | None) -> HistoryPage:
    params: dict = {"event_id": event.id, "limit": limit + 1}
    where = ["true"]
    if not scope.include_pending:
        where.append("NOT i.pending")
    if registration_id is not None:
        where.append("i.registration_id = :registration_id")
        params["registration_id"] = registration_id
    if not scope.all:
        either = []
        if scope.activity_ids is not None:
            either.append("i.activity_id = ANY(:activity_ids)")
            params["activity_ids"] = list(scope.activity_ids)
        if scope.registration_ids is not None:
            either.append("i.registration_id = ANY(:registration_ids)")
            params["registration_ids"] = list(scope.registration_ids)
        where.append(f"({' OR '.join(either)})" if either else "false")
    if before:
        at, item_id = decode_cursor(before)
        where.append("(i.at, i.item_id) < (:before_at, :before_id)")
        params.update(before_at=at, before_id=item_id)
    rows = (await db.execute(text(_ITEMS.format(where=" AND ".join(where))), params)).mappings().all()
    more = len(rows) > limit
    rows = rows[:limit]
    items = [
        HistoryItem(
            id=row["item_id"], at=row["at"], type=row["type"],
            registration=HistoryRegistration(id=str(row["registration_id"]), club_name=row["club_name"]),
            activity=(HistoryActivity(id=str(row["activity_id"]), name=row["activity_name"])
                      if row["activity_id"] is not None else None),
            points=float(row["points"]),
            previous_points=None if row["previous_points"] is None else float(row["previous_points"]),
            kind=row["kind"],
            actor=HistoryActor(name=row["actor_name"], as_=row["actor_as"]),
            reason=row["reason"], pending=bool(row["pending"]),
        )
        for row in rows
    ]
    cursor = encode_cursor(rows[-1]["at"], rows[-1]["item_id"]) if more and rows else None
    return HistoryPage(items=items, next_cursor=cursor)
