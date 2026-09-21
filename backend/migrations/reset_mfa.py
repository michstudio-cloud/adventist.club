"""Last resort: take the second factor off an account from the database.

    pip install "psycopg[binary]"
    DATABASE_URL=... python reset_mfa.py --email persona@ejemplo.org [--reason "..."] [--commit]

`POST /users/{id}/mfa-reset` is the normal way, but it needs a SECOND MASTER_GC.
While there is only one, losing the authenticator and the recovery codes would
lock the platform's owner out for good. This script is the way back in, and it
deliberately costs direct access to the production database.

It clears `mfa_enabled` / `mfa_secret`, deletes every recovery code of that
account and writes an `MFA_RESET` row in `audit_log` with no actor (nobody
signed in) so the reset is never invisible. Dry run unless --commit; running it
twice changes nothing after the first time.

It also lists, with --list, the MASTER_GC accounts that have not enrolled yet:
that is step one of the E1 deploy, before MASTER_MFA_ENFORCED is turned on.
"""
import argparse
import json
import os
import sys

import psycopg

PENDING_MASTERS = "SELECT email FROM users WHERE role = 'MASTER_GC' AND NOT mfa_enabled ORDER BY email"


def reset(conn, email: str, reason: str, *, commit: bool = False) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, role, mfa_enabled FROM users WHERE email = %s", (email,)
        )
        row = cur.fetchone()
        if row is None:
            return {"found": False, "email": email}
        user_id, name, role, mfa_enabled = row
        report = {
            "found": True,
            "email": email,
            "name": name,
            "role": role,
            "mfa_enabled": mfa_enabled,
            "committed": False,
        }
        cur.execute(
            "SELECT count(*) FROM mfa_recovery_codes WHERE user_id = %s", (user_id,)
        )
        report["recovery_codes"] = cur.fetchone()[0]
        if not commit:
            return report

        cur.execute(
            "UPDATE users SET mfa_enabled = false, mfa_secret = NULL, updated_at = now()"
            " WHERE id = %s",
            (user_id,),
        )
        cur.execute("DELETE FROM mfa_recovery_codes WHERE user_id = %s", (user_id,))
        cur.execute(
            "INSERT INTO audit_log (id, action, entity_type, entity_id, user_email, user_role,"
            " details, metadata_json) VALUES (gen_random_uuid(), 'MFA_RESET', 'USER', %s, %s, %s,"
            " %s, %s)",
            (
                str(user_id),
                email,
                role,
                "MFA reset from migrations/reset_mfa.py (direct database access)",
                json.dumps({"reason": reason, "via": "script"}),
            ),
        )
        conn.commit()
        report["committed"] = True
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", help="account whose second factor is cleared")
    parser.add_argument("--reason", default="Recuperación de acceso del titular")
    parser.add_argument("--list", action="store_true", help="MASTER_GC accounts without MFA")
    parser.add_argument("--commit", action="store_true", help="apply (default: dry run)")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        sys.exit("Set DATABASE_URL")

    with psycopg.connect(database_url) as conn:
        if args.list:
            with conn.cursor() as cur:
                cur.execute(PENDING_MASTERS)
                pending = [email for (email,) in cur.fetchall()]
            print(json.dumps({"masters_without_mfa": pending}, indent=2, ensure_ascii=False))
            if not args.email:
                return
        if not args.email:
            sys.exit("Set --email (or use --list)")
        report = reset(conn, args.email.strip().lower(), args.reason, commit=args.commit)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["found"]:
        sys.exit(f"No account with email {args.email}")
    if not args.commit:
        print("\nDry run. Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
