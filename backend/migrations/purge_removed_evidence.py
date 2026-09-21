"""Delete from the PRIVATE bucket the objects of evidence nobody can see any more.

    pip install "psycopg[binary]" boto3
    DATABASE_URL=... R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \\
    R2_PRIVATE_BUCKET_NAME=... python purge_removed_evidence.py [--days 30] [--commit]

The API only removes evidence logically (status REMOVED) and an upload that was never
confirmed stays PENDING_UPLOAD. This script purges both once they are older than --days:
REMOVED counts from `removed_at`, PENDING_UPLOAD from `created_at`. ACTIVE evidence is never
touched. Dry run unless --commit (the dry run needs no R2 credentials and lists what would go).
With --commit each object is deleted and its row is stamped `purged_at` (a stale
PENDING_UPLOAD also becomes REMOVED), so a second run finds nothing: idempotent. Rows are
kept: they are the record that the evidence existed; audit_log has who added and removed it.
"""
import argparse, json, os, sys

import psycopg

CANDIDATES = """
    SELECT id, storage_key, status FROM evidences
    WHERE purged_at IS NULL
      AND ((status = 'REMOVED' AND removed_at < now() - make_interval(days => %(days)s))
        OR (status = 'PENDING_UPLOAD' AND created_at < now() - make_interval(days => %(days)s)))
    ORDER BY created_at
"""


def r2_client():
    import boto3
    from botocore.config import Config

    missing = [n for n in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY") if not os.environ.get(n)]
    if missing:
        sys.exit(f"Set {', '.join(missing)}")
    return boto3.client(
        "s3", endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto", config=Config(signature_version="s3v4"))


def purge(conn, client, bucket, *, days=30, commit=False):
    """`client` is only used with commit=True. Returns the report that main() prints."""
    report = {"removed": 0, "pending_upload": 0, "purged": 0, "failed": [], "keys": []}
    with conn.cursor() as cur:
        cur.execute(CANDIDATES, {"days": days})
        for evidence_id, key, status in cur.fetchall():
            report["removed" if status == "REMOVED" else "pending_upload"] += 1
            if not key.startswith("evidence/"):      # never delete outside the evidence prefix
                report["failed"].append({"key": key, "error": "unexpected key"})
                continue
            if not commit:
                report["keys"].append(key)
                continue
            try:
                client.delete_object(Bucket=bucket, Key=key)   # a missing object is not an error in S3/R2
            except Exception as exc:                            # keep going: the row stays for the next run
                report["failed"].append({"key": key, "error": str(exc)})
                continue
            cur.execute("UPDATE evidences SET purged_at = now(), status = 'REMOVED',"
                        " removed_at = COALESCE(removed_at, now()) WHERE id = %s", (evidence_id,))
            report["purged"] += 1
    if commit:
        conn.commit()
    else:
        conn.rollback()
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    if args.days < 1:
        sys.exit("--days must be at least 1")
    bucket = os.environ.get("R2_PRIVATE_BUCKET_NAME")
    if args.commit and not bucket:
        sys.exit("Set R2_PRIVATE_BUCKET_NAME")
    client = r2_client() if args.commit else None
    with psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://")) as conn:
        report = purge(conn, client, bucket, days=args.days, commit=args.commit)
    print(json.dumps({**report, "days": args.days, "committed": args.commit}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
