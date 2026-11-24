-- 034_honor_level.sql — additive, idempotent. (Drafted as 032; 032 and 033 are left to the open PRs for passkeys and the event node.)
--
-- `honors.level_slug`: the level an honor belongs to, as the slug of the class program it is suggested for.
-- Adventurer awards: corderitos, aves-madrugadoras, abejas-industriosas, rayos-de-sol, constructor,
-- manos-ayudadoras (the program slugs of data/adventurer_classes.json) or multinivel.
-- It is NOT `skill_level` (1–3, Pathfinder honors: XP bonus, «Nivel n» on the sheet) nor `difficulty_level`
-- (BEGINNER/INTERMEDIATE/ADVANCED, CHECK constraint).
--
-- Order: either. The API (routers/honors.py, `level_column_ready`) looks the column up before using it: while
-- it is missing, `level_slug` comes back null and `GET /honors?level=…` is ignored; within a minute of this file
-- being applied it is picked up, no restart needed. import_adventurer_awards.py writes it and needs this file.
ALTER TABLE honors ADD COLUMN IF NOT EXISTS level_slug varchar(40);
CREATE INDEX IF NOT EXISTS ix_honors_ministry_level ON honors (ministry_id, level_slug) WHERE level_slug IS NOT NULL;

-- Backfill of the 164 Adventurer awards from their Spanish description (written by
-- import_adventurer_awards.py: «…Clase sugerida: <clase>.» or «Multinivel: no está asignada a una clase.»).
-- Only rows still without a level; data/adventurer_awards_descriptions.csv has the same mapping per slug.
UPDATE honors h SET level_slug = CASE
    WHEN h.description LIKE '%Clase sugerida: Corderitos.%'          THEN 'corderitos'
    WHEN h.description LIKE '%Clase sugerida: Aves Madrugadoras.%'   THEN 'aves-madrugadoras'
    WHEN h.description LIKE '%Clase sugerida: Abejas Industriosas.%' THEN 'abejas-industriosas'
    WHEN h.description LIKE '%Clase sugerida: Rayos de Sol.%'        THEN 'rayos-de-sol'
    WHEN h.description LIKE '%Clase sugerida: Constructor.%'         THEN 'constructor'
    WHEN h.description LIKE '%Clase sugerida: Manos Ayudadoras.%'    THEN 'manos-ayudadoras'
    WHEN h.description LIKE '%Multinivel: no está asignada a una clase.%' THEN 'multinivel'
  END
  FROM ministries m
  WHERE h.ministry_id = m.id AND m.slug = 'adventurers' AND h.level_slug IS NULL;
