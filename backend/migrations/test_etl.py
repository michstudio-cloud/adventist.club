"""End-to-end check of etl_mongo_to_postgres.py against mongomock + a throwaway Postgres.

    pip install mongomock "psycopg[binary]" pymongo
    DATABASE_URL=postgresql://test@127.0.0.1:55432/etl python test_etl.py

DATABASE_URL must point at a disposable database with 001_identity_org_honors.sql applied.
"""
import os, sys
from datetime import datetime
from unittest import mock

import mongomock, psycopg
from bson import ObjectId

import etl_mongo_to_postgres as etl

oid = lambda n: ObjectId(f"{n:024x}")
client = mongomock.MongoClient()
db = client["adventist_club"]
DIV, UNI, CLUB, ORPHAN = oid(1), oid(2), oid(3), oid(4)
U_DIR, U_DUP, U_BADROLE, U_NOPASS, U_KID = oid(11), oid(12), oid(13), oid(14), oid(15)
S1, S2, S3 = oid(21), oid(22), oid(23)

db.org_nodes.insert_many([
    {"_id": DIV, "name": "División Test", "type": "DIVISION", "parent_id": None, "status": "ACTIVE"},
    {"_id": UNI, "name": "Unión Test", "type": "UNION", "parent_id": str(DIV), "status": "ACTIVE"},   # str ref
    {"_id": CLUB, "name": "Club Orión", "type": "CLUB", "parent_id": UNI, "status": "ACTIVE",          # ObjectId ref
     "location": {"type": "Point", "coordinates": [-95.36, 29.76]}, "city": "Houston",
     "metadata": {"director": "Ana", "capacity": 40}},
    {"_id": ORPHAN, "name": "Huérfano", "type": "CHURCH", "parent_id": str(oid(999)), "status": "INACTIVE"},
])
db.users.insert_many([
    {"_id": U_DIR, "email": "Director@Test.com", "password_hash": "$2b$12$abc", "name": "Dir", "role": "CLUB_DIRECTOR",
     "org_node_id": str(CLUB), "created_at": datetime(2026, 1, 1), "child_protection_cert": {"completed": True,
     "completed_at": datetime(2026, 2, 1)}, "birth_date": datetime(1990, 5, 17)},
    {"_id": U_DUP, "email": "director@test.com", "password_hash": "x", "name": "Dup", "role": "STUDENT",
     "created_at": datetime(2026, 1, 2)},
    {"_id": U_BADROLE, "email": "raro@test.com", "password_hash": "x", "name": "Raro", "role": "WIZARD",
     "org_node_id": str(oid(998)), "created_at": datetime(2026, 1, 3)},
    {"_id": U_NOPASS, "email": "nopass@test.com", "name": "NoPass", "role": "STUDENT", "created_at": datetime(2026, 1, 4)},
    {"_id": U_KID, "email": "kid@test.com", "password_hash": "x", "name": "Kid", "role": "STUDENT", "is_minor": True,
     "created_at": datetime(2026, 1, 5)},
])
db.guardianships.insert_many([
    {"guardian_id": str(U_DIR), "child_id": str(U_KID), "relationship": "PARENT", "consent_status": "APPROVED"},
    {"guardian_id": str(U_DIR), "child_id": str(U_NOPASS)},   # child was skipped -> must be skipped too
])
req = {"id": "1.0", "order": 1, "description": "Explicar RCP", "question_bank": [
    {"id": "1.0", "question_text": "¿Compresiones/min?", "question_type": "MULTIPLE_CHOICE",
     "options": ["60", "100-120"], "correct_answer": "100-120"}]}
db.specialties.insert_many([
    {"_id": S1, "name": "Nudos", "code": "REC-001", "category": "RECREATION", "status": "ARCHIVED",   # slug clash w/ existing row
     "created_by_id": str(U_DIR), "created_at": datetime(2026, 3, 1), "version_metadata": {"version": 1}},
    {"_id": S2, "name": "Nudos", "code": "REC-001", "category": "RECREATION", "status": "PUBLISHED",  # dup code + dup name
     "created_by_id": str(U_DIR), "created_at": datetime(2026, 3, 2), "requirements": [req],
     "review_history": [{"reviewer_id": str(U_DIR), "reviewer_name": "Dir", "action": "APPROVED"},
                        {"action": "BOGUS"}],
     "resources": [{"name": "Manual", "url": "https://media.adventist.club/resources/a.pdf", "type": "pdf"}, {"name": "sin url"}],
     "patch_image_url": "https://media.adventist.club/patches/nudos.png",
     "version_metadata": {"version": 2, "previous_version_id": str(S1)}},
    {"_id": S3, "name": "Primeros Auxilios", "code": "HS-001", "category": "NOPE", "status": "DRAFT",
     "org_scope_id": str(CLUB), "created_at": datetime(2026, 3, 3)},
])
db.audit_log.insert_one({"action": "PUBLISH", "entity_type": "SPECIALTY", "entity_id": str(S2), "user_id": str(U_DIR),
                         "user_email": "director@test.com", "metadata": {"at": datetime(2026, 3, 2)}})

PG = os.environ["DATABASE_URL"]


def run(*argv):
    etl.report.clear(); etl.warnings.clear()
    with mock.patch.object(etl, "MongoClient", lambda *a, **k: client), \
         mock.patch.dict(os.environ, {"MONGODB_URI": "mongodb://mock"}), \
         mock.patch.object(sys, "argv", ["etl", *argv]):
        etl.main()
    return dict(etl.report), list(etl.warnings)


def q(sql):
    with psycopg.connect(PG) as c:
        return c.execute(sql).fetchall()


def counts():
    return {t: q(f"SELECT count(*) FROM {t}")[0][0] for t in
            ("organizations", "users", "guardianships", "honors", "honor_requirements",
             "honor_questions", "honor_reviews", "honor_resources", "audit_log")}


before = counts()
run()                                   # dry run
assert counts() == before, "dry run must not persist anything"

rows, warns = run("--commit")
after = counts()
assert after["organizations"] - before["organizations"] == 4
assert after["users"] == 3 and rows["users_skipped"] == 2, (after, rows)
assert after["guardianships"] == 1 and rows["guardianships_skipped"] == 1
assert after["honors"] - before["honors"] == 3
assert (after["honor_requirements"], after["honor_questions"], after["honor_reviews"], after["honor_resources"]) == (1, 1, 1, 1)

path_depth = dict(q("SELECT name, nlevel(path) FROM organizations WHERE legacy_mongo_id IS NOT NULL"))
assert path_depth == {"División Test": 1, "Unión Test": 2, "Club Orión": 3, "Huérfano": 1}, path_depth
assert q("SELECT count(*) FROM organizations o JOIN organizations d ON d.name='División Test' WHERE o.path <@ d.path")[0][0] == 3
assert q("SELECT longitude, latitude, metadata_json->>'director' FROM organizations WHERE name='Club Orión'")[0] == (-95.36, 29.76, "Ana")

u = q("SELECT email, role, birth_date::text, child_protection_completed, o.name FROM users u LEFT JOIN organizations o ON o.id=u.organization_id WHERE u.name='Dir'")[0]
assert u == ("director@test.com", "CLUB_DIRECTOR", "1990-05-17", True, "Club Orión"), u
assert q("SELECT role, organization_id FROM users WHERE name='Raro'")[0] == ("STUDENT", None)

honors = {r[0]: r[1:] for r in q("SELECT legacy_mongo_id, slug, code, status, active, version FROM honors WHERE legacy_mongo_id IS NOT NULL")}
assert honors[str(S1)] == ("nudos-2", "REC-001", "ARCHIVED", False, 1), honors[str(S1)]     # existing 'nudos' keeps its slug
assert honors[str(S2)] == ("nudos-3", None, "PUBLISHED", True, 2), honors[str(S2)]          # duplicate code cleared
assert q("SELECT p.legacy_mongo_id FROM honors h JOIN honors p ON p.id=h.previous_version_id WHERE h.legacy_mongo_id=%s" % repr(str(S2)))[0][0] == str(S1)
assert q("SELECT category_id, (SELECT name FROM organizations WHERE id=org_scope_id) FROM honors WHERE code='HS-001'")[0] == (None, "Club Orión")
assert q("SELECT slug, status FROM honors WHERE legacy_mongo_id IS NULL") == [("nudos", "PUBLISHED")], "pre-existing honor untouched"
assert q("SELECT action FROM honor_reviews")[0][0] == "APPROVE"
assert q("SELECT u.name FROM audit_log a JOIN users u ON u.id=a.user_id")[0][0] == "Dir"

run("--commit")                         # second run must be a no-op in row counts
assert counts() == after, (counts(), after)
print("ETL OK:", after)
print(f"{len(warns)} warnings on dirty fixtures, e.g.:"); [print("  -", w) for w in warns[:8]]
