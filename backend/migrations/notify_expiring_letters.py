"""Warn a club leader that their church letter is about to expire (E7, decision D5).

    pip install "psycopg[binary]" resend
    DATABASE_URL=... RESEND_API_KEY=... python notify_expiring_letters.py [--days 30] [--commit]

Meant for a Render Cron Job once the owner turns it on. Dry run by default: it
prints who WOULD be written to and sends nothing.

Idempotent thanks to `notification_log`: one warning per letter per window, so
running it every day writes to each person once. The panel shows the same
warning from `GET /church-letters/me` (`expires_soon`) without any script.

The message goes to the leader about their OWN document. No minor is named and
nothing is said to anybody else.
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys
import uuid

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

KIND = "LEADER_LETTER_EXPIRING"

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")

    from app.services.email import send_letter_expiring_email

    report = {"mode": "commit" if args.commit else "dry-run", "sent": 0, "skipped": 0, "people": []}
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(CANDIDATES, {"days": args.days, "kind": KIND})
        for user_id, email, name, valid_until, letter_id in cur.fetchall():
            days_left = (valid_until - __import__("datetime").date.today()).days
            report["people"].append({"email": email, "valid_until": str(valid_until)})
            if not args.commit:
                report["skipped"] += 1
                continue
            ok = asyncio.run(
                send_letter_expiring_email(email, name, valid_until.isoformat(), days_left)
            )
            cur.execute(
                "INSERT INTO notification_log (id, user_id, email, kind, entity_type, entity_id,"
                " sent_at, ok) VALUES (%s, %s, %s, %s, 'CHURCH_LETTER', %s, now(), %s)",
                (uuid.uuid4(), user_id, email, KIND, str(letter_id), ok),
            )
            report["sent"] += 1
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
