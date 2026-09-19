"""Write honors.image_url from uploaded.json (file -> public URL). Dry run unless --commit."""
import json, pathlib, sys
import psycopg
env=dict(l.split("=",1) for l in (pathlib.Path.home()/".adventist-migration.env").read_text().splitlines() if "=" in l)
up=json.load(open("uploaded.json")); commit="--commit" in sys.argv
with psycopg.connect(env["DATABASE_URL"].strip()) as c, c.cursor() as cur:
    cur.execute("SELECT id FROM ministries WHERE slug='pathfinders'"); ministry=cur.fetchone()[0]
    hit=miss=0
    for f,v in up.items():
        cur.execute("UPDATE honors SET image_url=%s, updated_at=now() WHERE ministry_id=%s AND slug=%s",(v["url"],ministry,f[:-5]))
        hit+=cur.rowcount; miss+=cur.rowcount==0
    cur.execute("SELECT count(*) FILTER (WHERE image_url IS NOT NULL), count(*) FROM honors WHERE ministry_id=%s",(ministry,))
    with_img,total=cur.fetchone()
    (c.commit if commit else c.rollback)()
print(json.dumps({"mode":"COMMIT" if commit else "DRY RUN","uploaded_files":len(up),"rows_updated":hit,"files_without_honor":miss,"honors_with_image":with_img,"honors_total":total}))
