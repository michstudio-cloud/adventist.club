"""One-off ETL: legacy MongoDB (adventist_club) -> Neon PostgreSQL.

Requires migrations/001_identity_org_honors.sql to be applied first.

    pip install "pymongo[srv]" "psycopg[binary]"
    MONGODB_URI=... DATABASE_URL=... python etl_mongo_to_postgres.py            # dry run (ROLLBACK)
    MONGODB_URI=... DATABASE_URL=... python etl_mongo_to_postgres.py --commit   # persist

Mongo is only read. Postgres writes are one transaction. Re-runnable: rows are keyed
by legacy_mongo_id and new ids are uuid5(legacy id), so a second run updates in place.
email_verifications is intentionally not migrated (short-lived tokens).
"""
import argparse, json, os, re, sys, unicodedata, uuid
from collections import Counter
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb
from pymongo import MongoClient

NS = uuid.UUID("6f1d2c0e-5a7b-4c1e-9d3a-ad7e27157c1b")
ROLES = {"MASTER_GC", "ADMIN_DIVISION", "ADMIN_UNION", "ADMIN_ASSOCIATION", "COORDINATOR_ZONE",
         "CLUB_DIRECTOR", "INSTRUCTOR", "STUDENT", "PARENT_GUARDIAN"}
CATEGORY_SLUGS = {
    "ARTS_CRAFTS_HOBBIES": "arts-crafts-hobbies", "HEALTH_SCIENCE": "health-science",
    "HOUSEHOLD_ARTS": "household-arts", "NATURE": "nature",
    "OUTDOOR_INDUSTRIES": "outdoor-industries", "RECREATION": "recreation",
    "SPIRITUAL_GROWTH": "spiritual-growth", "VOCATIONAL": "vocational",
}
REVIEW_ACTIONS = {"APPROVED": "APPROVE", "REJECTED": "REJECT", "APPROVE": "APPROVE",
                  "REJECT": "REJECT", "REQUEST_CHANGES": "REQUEST_CHANGES"}
report = Counter()
warnings = []


def warn(msg):
    warnings.append(msg)


def new_id(legacy):
    return uuid.uuid5(NS, str(legacy))


def hexid(value):
    """Mongo stored references both as ObjectId and as str; normalise to 24-hex or None."""
    if value in (None, ""):
        return None
    s = str(value)
    return s if re.fullmatch(r"[0-9a-fA-F]{24}", s) else None


def utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def slugify(value):
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "especialidad"


def migrate_orgs(mdb, cur):
    nodes = {str(d["_id"]): d for d in mdb.org_nodes.find({})}
    parent_of = {k: hexid(d.get("parent_id")) for k, d in nodes.items()}

    def chain(k):
        seen, out = set(), []
        while k and k in nodes and k not in seen:
            seen.add(k)
            out.append(k)
            k = parent_of.get(k)
        return list(reversed(out))

    # parents before children so the self-FK always resolves
    for k in sorted(nodes, key=lambda k: len(chain(k))):
        d = nodes[k]
        parent = parent_of[k]
        if parent and parent not in nodes:
            warn(f"org_node {k}: parent {parent} not found -> migrated as root")
            parent = None
        coords = ((d.get("location") or {}).get("coordinates") or [None, None]) + [None, None]
        path = ".".join(new_id(x).hex for x in chain(k))
        cur.execute(
            """INSERT INTO organizations (id,parent_id,type,name,status,path,city,state,country,
                                          longitude,latitude,metadata_json,legacy_mongo_id,created_at,updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now()),COALESCE(%s,now()))
               ON CONFLICT (id) DO UPDATE SET parent_id=EXCLUDED.parent_id,type=EXCLUDED.type,
                 name=EXCLUDED.name,status=EXCLUDED.status,path=EXCLUDED.path,city=EXCLUDED.city,
                 state=EXCLUDED.state,country=EXCLUDED.country,longitude=EXCLUDED.longitude,
                 latitude=EXCLUDED.latitude,metadata_json=EXCLUDED.metadata_json,updated_at=now()""",
            (new_id(k), new_id(parent) if parent else None, str(d.get("type", "")).lower(),
             d.get("name") or "(sin nombre)", str(d.get("status", "ACTIVE")).lower(), path,
             d.get("city"), d.get("state"), d.get("country"), coords[0], coords[1],
             Jsonb(d["metadata"]) if d.get("metadata") else None, k,
             utc(d.get("created_at")), utc(d.get("updated_at"))))
        report["organizations"] += 1
    return set(nodes)


def migrate_users(mdb, cur, org_ids):
    seen_email, migrated = {}, set()
    for d in mdb.users.find({}).sort("created_at", 1):
        k = str(d["_id"])
        email = (d.get("email") or "").strip().lower()
        if not email or not d.get("password_hash"):
            warn(f"user {k}: missing email or password_hash -> skipped")
            report["users_skipped"] += 1
            continue
        if email in seen_email:
            warn(f"user {k}: duplicate email of {seen_email[email]} -> skipped")
            report["users_skipped"] += 1
            continue
        seen_email[email] = k
        role = d.get("role") if d.get("role") in ROLES else "STUDENT"
        if role != d.get("role"):
            warn(f"user {k}: unknown role {d.get('role')!r} -> STUDENT")
        org = hexid(d.get("org_node_id"))
        if org and org not in org_ids:
            warn(f"user {k}: org_node {org} not found -> no organization")
            org = None
        cpc = d.get("child_protection_cert") or {}
        birth = utc(d.get("birth_date"))
        cur.execute(
            """INSERT INTO users (id,email,password_hash,name,avatar_url,role,organization_id,is_minor,
                                  birth_date,mfa_secret,mfa_enabled,verification_status,
                                  child_protection_completed,child_protection_completed_at,status,
                                  last_login,legacy_mongo_id,created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now()))
               ON CONFLICT (id) DO UPDATE SET email=EXCLUDED.email,password_hash=EXCLUDED.password_hash,
                 name=EXCLUDED.name,avatar_url=EXCLUDED.avatar_url,role=EXCLUDED.role,
                 organization_id=EXCLUDED.organization_id,is_minor=EXCLUDED.is_minor,
                 birth_date=EXCLUDED.birth_date,mfa_secret=EXCLUDED.mfa_secret,
                 mfa_enabled=EXCLUDED.mfa_enabled,verification_status=EXCLUDED.verification_status,
                 child_protection_completed=EXCLUDED.child_protection_completed,
                 child_protection_completed_at=EXCLUDED.child_protection_completed_at,
                 status=EXCLUDED.status,last_login=EXCLUDED.last_login,updated_at=now()""",
            (new_id(k), email, d["password_hash"], d.get("name") or email, d.get("avatar_url"), role,
             new_id(org) if org else None, bool(d.get("is_minor")), birth.date() if birth else None,
             d.get("mfa_secret"), bool(d.get("mfa_enabled")), d.get("verification_status") or "PENDING",
             bool(cpc.get("completed")), utc(cpc.get("completed_at")), d.get("status") or "ACTIVE",
             utc(d.get("last_login")), k, utc(d.get("created_at"))))
        migrated.add(k)
        report["users"] += 1
    return migrated


def migrate_guardianships(mdb, cur, user_ids):
    for d in mdb.guardianships.find({}):
        k, g, c = str(d["_id"]), hexid(d.get("guardian_id")), hexid(d.get("child_id"))
        if g not in user_ids or c not in user_ids or g == c:
            warn(f"guardianship {k}: guardian/child not migrated -> skipped")
            report["guardianships_skipped"] += 1
            continue
        cur.execute(
            """INSERT INTO guardianships (id,guardian_id,child_id,relationship,consent_status,
                                          consent_granted_at,legacy_mongo_id,created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now()))
               ON CONFLICT (guardian_id,child_id) DO UPDATE SET relationship=EXCLUDED.relationship,
                 consent_status=EXCLUDED.consent_status,consent_granted_at=EXCLUDED.consent_granted_at""",
            (new_id(k), new_id(g), new_id(c), d.get("relationship") or "PARENT",
             d.get("consent_status") or "PENDING", utc(d.get("consent_granted_at")), k,
             utc(d.get("created_at"))))
        report["guardianships"] += 1


def migrate_honors(mdb, cur, org_ids, user_ids):
    cur.execute("SELECT id FROM ministries WHERE slug='pathfinders'")
    ministry = cur.fetchone()[0]
    cur.execute("SELECT slug,id FROM honor_categories WHERE ministry_id=%s", (ministry,))
    categories = dict(cur.fetchall())
    cur.execute("SELECT slug,legacy_mongo_id FROM honors WHERE ministry_id=%s", (ministry,))
    slug_owner = dict(cur.fetchall())
    codes, docs = {}, list(mdb.specialties.find({}).sort("created_at", 1))
    for d in docs:
        k = str(d["_id"])
        base = slugify(d.get("name"))
        slug, i = base, 2
        while slug in slug_owner and slug_owner[slug] != k:
            slug, i = f"{base}-{i}", i + 1
        slug_owner[slug] = k
        code = (d.get("code") or "").strip() or None
        if code and codes.setdefault(code, k) != k:
            warn(f"specialty {k}: duplicate code {code!r} (first {codes[code]}) -> code cleared")
            code = None
        status = d.get("status") or "DRAFT"
        vm = d.get("version_metadata") or {}
        creator = hexid(d.get("created_by_id"))
        orgref = lambda f: (lambda v: new_id(v) if v in org_ids else None)(hexid(d.get(f)))
        hid = new_id(k)
        cur.execute(
            """INSERT INTO honors (id,ministry_id,category_id,name,slug,image_url,thumbnail_url,active,code,
                 description,difficulty_level,honor_type,status,org_scope_id,estimated_hours,
                 exam_passing_score,exam_time_limit_minutes,max_exam_attempts,created_by_id,
                 approved_zone_org_id,approved_association_org_id,version,changes_description,
                 published_at,legacy_mongo_id,created_at,updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       COALESCE(%s,now()),COALESCE(%s,now()))
               ON CONFLICT (id) DO UPDATE SET category_id=EXCLUDED.category_id,name=EXCLUDED.name,
                 image_url=EXCLUDED.image_url,thumbnail_url=EXCLUDED.thumbnail_url,active=EXCLUDED.active,
                 code=EXCLUDED.code,description=EXCLUDED.description,
                 difficulty_level=EXCLUDED.difficulty_level,honor_type=EXCLUDED.honor_type,
                 status=EXCLUDED.status,org_scope_id=EXCLUDED.org_scope_id,
                 estimated_hours=EXCLUDED.estimated_hours,exam_passing_score=EXCLUDED.exam_passing_score,
                 exam_time_limit_minutes=EXCLUDED.exam_time_limit_minutes,
                 max_exam_attempts=EXCLUDED.max_exam_attempts,created_by_id=EXCLUDED.created_by_id,
                 approved_zone_org_id=EXCLUDED.approved_zone_org_id,
                 approved_association_org_id=EXCLUDED.approved_association_org_id,version=EXCLUDED.version,
                 changes_description=EXCLUDED.changes_description,published_at=EXCLUDED.published_at,
                 updated_at=now()""",
            (hid, ministry, categories.get(CATEGORY_SLUGS.get(d.get("category"))), d.get("name") or slug, slug,
             d.get("patch_image_url"), d.get("thumbnail_url"), status != "ARCHIVED", code,
             d.get("description"), d.get("difficulty_level"), d.get("type"), status, orgref("org_scope_id"),
             d.get("estimated_hours"), d.get("exam_passing_score") or 80, d.get("exam_time_limit_minutes"),
             d.get("max_exam_attempts") or 3, new_id(creator) if creator in user_ids else None,
             orgref("approved_by_zone_id"), orgref("approved_by_association_id"), vm.get("version") or 1,
             vm.get("changes_description"), utc(d.get("published_at")), k,
             utc(d.get("created_at")), utc(d.get("updated_at"))))
        report["honors"] += 1

        # children are fully owned by the legacy document: rebuild them on every run
        for table in ("honor_requirements", "honor_reviews", "honor_resources"):
            cur.execute(f"DELETE FROM {table} WHERE honor_id=%s", (hid,))
        for pos, r in enumerate(d.get("requirements") or [], 1):
            rid = uuid.uuid4()
            cur.execute("""INSERT INTO honor_requirements (id,honor_id,position,description,is_theoretical,instructions)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (rid, hid, r.get("order") or pos, r.get("description") or "",
                         r.get("is_theoretical", True), r.get("instructions")))
            report["honor_requirements"] += 1
            for qpos, q in enumerate(r.get("question_bank") or [], 1):
                cur.execute("""INSERT INTO honor_questions (requirement_id,position,question_text,question_type,
                                 options,correct_answer,points,explanation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (rid, qpos, q.get("question_text") or "", q.get("question_type") or "SHORT_ANSWER",
                             Jsonb(q["options"]) if q.get("options") else None,
                             str(q.get("correct_answer", "")), q.get("points") or 1, q.get("explanation")))
                report["honor_questions"] += 1
        for rv in d.get("review_history") or []:
            action = REVIEW_ACTIONS.get(rv.get("action"))
            if not action:
                warn(f"specialty {k}: unknown review action {rv.get('action')!r} -> skipped")
                continue
            reviewer = hexid(rv.get("reviewer_id"))
            cur.execute("""INSERT INTO honor_reviews (honor_id,reviewer_id,reviewer_name,reviewer_role,action,
                             comments,reviewed_at) VALUES (%s,%s,%s,%s,%s,%s,COALESCE(%s,now()))""",
                        (hid, new_id(reviewer) if reviewer in user_ids else None, rv.get("reviewer_name"),
                         rv.get("reviewer_role"), action, rv.get("comments"), utc(rv.get("reviewed_at"))))
            report["honor_reviews"] += 1
        for pos, res in enumerate(d.get("resources") or [], 1):
            if not res.get("url"):
                continue
            cur.execute("INSERT INTO honor_resources (honor_id,position,name,url,type) VALUES (%s,%s,%s,%s,%s)",
                        (hid, pos, res.get("name") or res["url"], res["url"], res.get("type")))
            report["honor_resources"] += 1

    known = {str(d["_id"]) for d in docs}
    for d in docs:  # second pass: version chain needs every honor to exist
        prev = hexid((d.get("version_metadata") or {}).get("previous_version_id"))
        if prev in known:
            cur.execute("UPDATE honors SET previous_version_id=%s WHERE id=%s", (new_id(prev), new_id(d["_id"])))


def migrate_audit(mdb, cur, org_ids, user_ids):
    for d in mdb.audit_log.find({}):
        k, u, o = str(d["_id"]), hexid(d.get("user_id")), hexid(d.get("org_scope_id"))
        cur.execute(
            """INSERT INTO audit_log (id,action,entity_type,entity_id,user_id,user_email,user_role,
                                      organization_id,details,metadata_json,legacy_mongo_id,created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now())) ON CONFLICT (id) DO NOTHING""",
            (new_id(k), d.get("action") or "UNKNOWN", d.get("entity_type") or "UNKNOWN", str(d.get("entity_id", "")),
             new_id(u) if u in user_ids else None, d.get("user_email"), d.get("user_role"),
             new_id(o) if o in org_ids else None, d.get("details"),
             Jsonb(json.loads(json.dumps(d.get("metadata") or {}, default=str))), k, utc(d.get("created_at"))))
        report["audit_log"] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="persist changes (default: dry run, ROLLBACK)")
    ap.add_argument("--mongo-db", default=os.environ.get("MONGODB_DB_NAME", "adventist_club"))
    args = ap.parse_args()
    mongo_uri, pg_url = os.environ.get("MONGODB_URI"), os.environ.get("DATABASE_URL")
    if not mongo_uri or not pg_url:
        sys.exit("Set MONGODB_URI and DATABASE_URL in the environment.")
    mdb = MongoClient(mongo_uri, serverSelectionTimeoutMS=20000)[args.mongo_db]
    with psycopg.connect(pg_url.replace("postgresql+asyncpg://", "postgresql://")) as conn:
        with conn.cursor() as cur:
            org_ids = migrate_orgs(mdb, cur)
            user_ids = migrate_users(mdb, cur, org_ids)
            migrate_guardianships(mdb, cur, user_ids)
            migrate_honors(mdb, cur, org_ids, user_ids)
            migrate_audit(mdb, cur, org_ids, user_ids)
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps({"mode": "COMMIT" if args.commit else "DRY RUN (rolled back)",
                      "rows": dict(report), "warnings": len(warnings)}, indent=1))
    for w in warnings:
        print("WARN", w)


if __name__ == "__main__":
    main()
