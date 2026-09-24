"""Warn a club leader that their church letter is about to expire (E7, decision D5).

    pip install "psycopg[binary]" resend
    DATABASE_URL=... RESEND_API_KEY=... python notify_expiring_letters.py [--days 30] [--commit]

Meant for a Render Cron Job once the owner turns it on. Dry run by default: it
prints who WOULD be written to and sends nothing.

Idempotent thanks to `notification_log`: one warning per letter per window, so
running it every day writes to each person once. The panel shows the same
warning from `GET /church-letters/me` (`expires_soon`) without any script.

«Avisos»: every e-mail it sends also leaves the notice in the leader's in-app inbox
(`notifications`, kind LEADER_LETTER_EXPIRING, entity = the letter), in the same
transaction as its `notification_log` row — so the inbox has exactly the cap of the
e-mail: one per letter per window. While that notice is unread, a later warning about the
same letter updates it instead of piling up another row (the rule of `stage_inbox`).

The message goes to the leader about their OWN document. No minor is named and
nothing is said to anybody else.
"""
import argparse
import asyncio
import datetime
import json
import os
import pathlib
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

KIND = "LEADER_LETTER_EXPIRING"
ENTITY = "CHURCH_LETTER"
LINK = "/panel"

# The account is the truth (`users.leader_verified_until`); the letter is
# fetched with it so the log row is keyed by the document being renewed.
CANDIDATES = """
    SELECT u.id, u.email, u.name, u.leader_verified_until, l.id AS letter_id
      FROM users u
      JOIN church_letters l
        ON l.user_id = u.id AND l.status = 'AUTHORIZED'
       AND l.valid_until = u.leader_verified_until
     WHERE u.status = 'ACTIVE'
       AND u.leader_verified_until IS NOT NULL
       AND u.leader_verified_until BETWEEN current_date
           AND current_date + make_interval(days => %(days)s)
       AND NOT EXISTS (
             SELECT 1 FROM notification_log n
              WHERE n.kind = %(kind)s
                AND n.entity_id = l.id::text
                AND n.sent_at > now() - make_interval(days => %(days)s)
           )
     ORDER BY u.leader_verified_until
"""

UPDATE_UNREAD = """
    UPDATE notifications
       SET data = %(data)s, title = %(title)s, body = %(body)s, link = %(link)s,
           count = count + 1, created_at = now()
     WHERE id = (
             SELECT id FROM notifications
              WHERE user_id = %(user_id)s AND kind = %(kind)s AND entity_id = %(entity_id)s
                AND read_at IS NULL
              ORDER BY created_at DESC
              LIMIT 1
           )
"""

INSERT_NOTICE = """
    INSERT INTO notifications (id, user_id, kind, title, body, link, entity_type, entity_id,
                               count, data, created_at)
    VALUES (%(id)s, %(user_id)s, %(kind)s, %(title)s, %(body)s, %(link)s, %(entity_type)s,
            %(entity_id)s, 1, %(data)s, now())
"""


def stage_inbox(cur, *, user_id, letter_id, valid_until: datetime.date, days_left: int) -> None:
    """The in-app half of the warning, in the script's transaction. Same row shape and the
    same Spanish fallback text as `notifications.stage_inbox` (the app paints its own)."""
    from app.services.notifications import inbox_text

    data = {"valid_until": valid_until.isoformat(), "days": days_left}
    title, body = inbox_text(KIND, data, 1)
    params = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "kind": KIND,
        "title": title[:200],
        "body": body,
        "link": LINK,
        "entity_type": ENTITY,
        "entity_id": str(letter_id),
        "data": Jsonb(data),
    }
    cur.execute(UPDATE_UNREAD, params)
    if cur.rowcount == 0:
        cur.execute(INSERT_NOTICE, params)


def _send_email(email: str, name: str, valid_until: datetime.date, days_left: int) -> bool:
    from app.services.email import send_letter_expiring_email

    return asyncio.run(send_letter_expiring_email(email, name, valid_until.isoformat(), days_left))


def run(url: str, *, days: int = 30, commit: bool = False, send=_send_email) -> dict:
    """One pass. `send(email, name, valid_until, days_left) -> bool` is the e-mail (tests
    pass their own); nothing is sent, logged or put in an inbox without `commit`."""
    report = {"mode": "commit" if commit else "dry-run", "sent": 0, "skipped": 0, "people": []}
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(CANDIDATES, {"days": days, "kind": KIND})
        for user_id, email, name, valid_until, letter_id in cur.fetchall():
            days_left = (valid_until - datetime.date.today()).days
            report["people"].append({"email": email, "valid_until": str(valid_until)})
            if not commit:
                report["skipped"] += 1
                continue
            ok = send(email, name, valid_until, days_left)
            cur.execute(
                "INSERT INTO notification_log (id, user_id, email, kind, entity_type, entity_id,"
                " sent_at, ok) VALUES (%s, %s, %s, %s, %s, %s, now(), %s)",
                (uuid.uuid4(), user_id, email, KIND, ENTITY, str(letter_id), ok),
            )
            stage_inbox(cur, user_id=user_id, letter_id=letter_id, valid_until=valid_until, days_left=days_left)
            report["sent"] += 1
        if commit:
            conn.commit()
        else:
            conn.rollback()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    report = run(url, days=args.days, commit=args.commit)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
