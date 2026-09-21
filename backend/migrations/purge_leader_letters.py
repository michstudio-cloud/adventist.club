"""Delete from the PRIVATE bucket the church letters nobody may look at any more (E7).

    pip install "psycopg[binary]" boto3
    DATABASE_URL=... R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \\
    R2_PRIVATE_BUCKET_NAME=... python purge_leader_letters.py [--months 12] [--commit]

A church letter is a signed personal document. Once it has been REJECTED or
REVOKED, has expired, or was never completed, keeping the file serves nobody:
after --months the OBJECT goes and the ROW stays, because the row is the record
that the letter existed and `audit_log` has who decided what about it.

Dry run unless --commit (the dry run needs no R2 credentials and lists what
would go). Idempotent: a purged row is marked in `decision_note` and a second
run finds nothing. It never touches a letter that is still in force.
"""
import argparse
import json
import os
import sys

import psycopg

PURGED_MARK = "[archivo purgado]"

CANDIDATES = """
    SELECT id, storage_key, status, valid_until
      FROM church_letters
     WHERE COALESCE(decision_note, '') NOT LIKE %(mark)s
       AND (
             (status IN ('REJECTED', 'REVOKED')
              AND COALESCE(decided_at, updated_at) < now() - make_interval(months => %(months)s))
          OR (status = 'PENDING_UPLOAD'
              AND created_at < now() - make_interval(months => %(months)s))
          OR (status = 'AUTHORIZED' AND valid_until IS NOT NULL
              AND valid_until < (now() - make_interval(months => %(months)s))::date)
       )
     ORDER BY created_at
"""


def r2_client():
    import boto3
    from botocore.config import Config

    missing = [
        name
        for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        sys.exit(f"Set {', '.join(missing)}")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def purge(conn, client, bucket, *, months=12, commit=False):
    report = {"candidates": 0, "purged": 0, "failed": [], "keys": []}
    with conn.cursor() as cur:
        cur.execute(CANDIDATES, {"months": months, "mark": f"%{PURGED_MARK}%"})
        for letter_id, key, status, _valid_until in cur.fetchall():
            report["candidates"] += 1
            if not key or not key.startswith("letters/"):
                # Never delete outside the letters prefix of the private bucket.
                report["failed"].append({"key": key, "error": "unexpected key"})
                continue
            if not commit:
                report["keys"].append({"key": key, "status": status})
                continue
            try:
                client.delete_object(Bucket=bucket, Key=key)  # missing is not an error in S3/R2
            except Exception as exc:  # keep going: the row stays for the next run
                report["failed"].append({"key": key, "error": str(exc)})
                continue
            cur.execute(
                "UPDATE church_letters"
                " SET decision_note = left(COALESCE(decision_note || ' ', '') || %s, 2000),"
                "     updated_at = now()"
                " WHERE id = %s",
                (PURGED_MARK, letter_id),
            )
            report["purged"] += 1
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    bucket = os.environ.get("R2_PRIVATE_BUCKET_NAME")
    if args.commit and not bucket:
        sys.exit("Set R2_PRIVATE_BUCKET_NAME")

    client = r2_client() if args.commit else None
    with psycopg.connect(url) as conn:
        report = purge(conn, client, bucket, months=args.months, commit=args.commit)
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    report["mode"] = "commit" if args.commit else "dry-run"
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
