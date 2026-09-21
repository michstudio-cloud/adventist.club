-- 012_programs.sql
-- Bloque F · F1 — Programas (clases de Conquistadores y de Aventureros, Guía Mayor, EMC,
-- CMJA) sobre el MISMO motor del bloque A: catálogo paralelo a `honors` que alimenta las
-- tablas de progreso y evidencias que ya existen.
-- Spec: docs/superpowers/specs/2026-09-22-guias-mayores-aventureros-design.md §1.1–1.4
--
-- QUÉ CAMBIA
--   1. Seis tablas nuevas: programs, program_translations, program_sections,
--      program_section_translations, program_requirements, program_requirement_texts.
--   2. `honor_enrollments`: columna nueva `program_id`, `honor_id` pasa a admitir NULL y un
--      CHECK obliga a que haya EXACTAMENTE uno de los dos. Índices nuevos.
--   3. `requirement_progress`: columnas nuevas `kind`, `program_requirement_id`,
--      `satisfied_by_enrollment_id`; el CHECK de `completed_via` admite tres valores más.
--   4. `certificates`: columna nueva `program_id`.
--   5. Semilla idempotente de los ministerios `adventurers` y `master-guides`.
--
-- NADA se borra, se renombra ni se reescribe. Las 800+ especialidades, sus requisitos, sus
-- inscripciones y sus certificados no cambian de tabla, de columna ni de significado: una
-- inscripción de especialidad sigue siendo `honor_id NOT NULL, program_id NULL`, que es lo
-- que el CHECK nuevo exige. El CHECK se valida contra las filas vivas ANTES de crearse y la
-- migración falla en voz alta si alguna no lo cumple.
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO que mapea estas tablas (el backend nuevo lee
-- `honor_enrollments.program_id` y `requirement_progress.kind`).
-- Idempotente: se puede aplicar dos veces seguidas sin efecto.
--
-- VUELTA ATRÁS: mientras NO exista ninguna fila con `program_id IS NOT NULL`, se revierte
-- borrando las tablas nuevas, el CHECK y reponiendo `ALTER TABLE honor_enrollments ALTER
-- COLUMN honor_id SET NOT NULL`. En cuanto se inscriba la primera persona en un programa ya
-- no hay vuelta atrás: esas inscripciones tienen `honor_id NULL` a propósito.

-- ---------------------------------------------------------------------------
-- 0. Ministerios. `pathfinders` ya existe desde 001; los otros dos nacen aquí
--    para que un programa nunca cuelgue de un ministerio inventado por el código
--    (regla de ESTADO.md: el ministerio sale siempre del dato).
-- ---------------------------------------------------------------------------
INSERT INTO ministries (id, slug, name, status)
SELECT gen_random_uuid(), v.slug, v.name, 'active'
FROM (VALUES ('adventurers', 'Adventurers'), ('master-guides', 'Master Guides')) AS v(slug, name)
WHERE NOT EXISTS (SELECT 1 FROM ministries m WHERE m.slug = v.slug);

-- ---------------------------------------------------------------------------
-- 1. El catálogo de programas. Paralelo a `honors` a propósito: ninguna consulta
--    de especialidades se entera de que esta tabla existe.
--    Versionado igual que `honors`: publicado = inmutable; un cambio crea la
--    versión siguiente en DRAFT y archiva la anterior.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS programs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ministry_id uuid NOT NULL REFERENCES ministries(id),
  kind varchar(12) NOT NULL CHECK (kind IN ('CLASS', 'CURRICULUM')),
  slug varchar(120) NOT NULL,
  code varchar(40),
  name varchar(180) NOT NULL,
  description text,
  image_url text,
  sort_order smallint NOT NULL DEFAULT 0,
  -- A qué manual corresponde (decisión D2): GC, NAD, IAD, SAD… Se muestra en la ficha.
  authority varchar(10),
  status varchar(30) NOT NULL DEFAULT 'DRAFT'
    CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
  -- D3: quién firma la investidura. CLUB = director del club (clases); ASSOCIATION = la
  -- Asociación (GM / EMC / CMJA). La rama de concesión de ASSOCIATION llega con F4; hasta
  -- entonces la columna YA decide: un director de club no inviste un programa ASSOCIATION.
  issuer_level varchar(12) NOT NULL DEFAULT 'CLUB'
    CHECK (issuer_level IN ('CLUB', 'ASSOCIATION')),
  version integer NOT NULL DEFAULT 1,
  previous_version_id uuid REFERENCES programs(id),
  published_at timestamptz,
  -- Atribución obligatoria de la fuente (CC BY-SA 3.0 cuando viene de la wiki oficial).
  source varchar(40),
  source_url text,
  license varchar(40),
  created_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Un slug por ministerio (la versión N añade «-vN», igual que las especialidades).
CREATE UNIQUE INDEX IF NOT EXISTS programs_ministry_slug_key ON programs (ministry_id, slug);
CREATE UNIQUE INDEX IF NOT EXISTS programs_ministry_code_key
  ON programs (ministry_id, code) WHERE code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_programs_ministry_status ON programs (ministry_id, status, sort_order);

-- Los nombres de las clases cambian por territorio (Orientador / Pioneiro / Ranger):
-- salen de aquí, nunca del código.
CREATE TABLE IF NOT EXISTS program_translations (
  program_id uuid NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  locale varchar(16) NOT NULL,
  name varchar(180) NOT NULL,
  description text,
  source varchar(40),
  source_url text,
  license varchar(40),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (program_id, locale)
);

-- Las secciones son datos del programa, no un enum: «Liderazgo», «Estilo de vida»…
CREATE TABLE IF NOT EXISTS program_sections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  program_id uuid NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  position integer NOT NULL,
  slug varchar(80) NOT NULL,
  name varchar(180) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS program_sections_program_position_key
  ON program_sections (program_id, position);
CREATE UNIQUE INDEX IF NOT EXISTS program_sections_program_slug_key
  ON program_sections (program_id, slug);

CREATE TABLE IF NOT EXISTS program_section_translations (
  section_id uuid NOT NULL REFERENCES program_sections(id) ON DELETE CASCADE,
  locale varchar(16) NOT NULL,
  name varchar(180) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (section_id, locale)
);

-- ---------------------------------------------------------------------------
-- 2. La ESTRUCTURA de los requisitos: una fila por requisito, igual en todos los
--    idiomas. `position` es global dentro del programa (1…N cruzando secciones)
--    y es exactamente la `requirement_progress.requirement_position` del bloque A.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS program_requirements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  program_id uuid NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  section_id uuid NOT NULL REFERENCES program_sections(id) ON DELETE CASCADE,
  position integer NOT NULL,
  -- Lo que se muestra al miembro: «I.3», «4».
  label varchar(12) NOT NULL,
  -- COURSE queda reservado para F9 y NO se declara aquí: una columna sin comportamiento
  -- se pudre. HONOR / PROGRAM / HOURS llegan con F2 (013_activity_logs.sql).
  kind varchar(8) NOT NULL DEFAULT 'FREE'
    CHECK (kind IN ('FREE', 'HONOR', 'PROGRAM', 'HOURS')),
  -- Se copia a requirement_progress.is_practical al inscribirse (regla 1 del bloque A).
  evidence_required boolean NOT NULL DEFAULT false,
  target_honor_id uuid REFERENCES honors(id),
  target_category_id uuid REFERENCES honor_categories(id),
  target_program_id uuid REFERENCES programs(id),
  target_quantity numeric(5,1),
  activity_category varchar(12),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  -- Cada destino sólo se rellena con el `kind` que le corresponde.
  CONSTRAINT program_requirements_target_check CHECK (
    (kind = 'HONOR' OR (target_honor_id IS NULL AND target_category_id IS NULL))
    AND (kind = 'PROGRAM' OR target_program_id IS NULL)
    AND (kind = 'HOURS' OR (target_quantity IS NULL AND activity_category IS NULL))
    AND (kind <> 'HONOR' OR target_honor_id IS NULL OR target_category_id IS NULL)
    AND (kind <> 'PROGRAM' OR target_program_id IS NOT NULL)
    AND (kind <> 'HOURS' OR (target_quantity IS NOT NULL AND target_quantity > 0
                             AND activity_category IN ('SERVICE', 'ATTENDANCE')))
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS program_requirements_program_position_key
  ON program_requirements (program_id, position);
CREATE INDEX IF NOT EXISTS ix_program_requirements_section
  ON program_requirements (section_id, position);

-- El TEXTO, por idioma. Estructura y texto van separados (a diferencia de
-- `honor_requirements`, que repite la fila por idioma) porque aquí la fila tiene tipo y
-- destino, y eso no puede diferir entre idiomas.
CREATE TABLE IF NOT EXISTS program_requirement_texts (
  requirement_id uuid NOT NULL REFERENCES program_requirements(id) ON DELETE CASCADE,
  locale varchar(16) NOT NULL,
  description text NOT NULL,
  instructions text,
  source varchar(40),
  source_url text,
  license varchar(40),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (requirement_id, locale)
);

-- ---------------------------------------------------------------------------
-- 3. Bloque A: la inscripción pasa a ser de una especialidad O de un programa.
--    Orden deliberado: primero la columna, luego la comprobación contra los datos
--    vivos, luego el CHECK y sólo al final se relaja el NOT NULL. Así no existe ni
--    un instante en el que `honor_id` admita NULL sin que el CHECK lo vigile.
-- ---------------------------------------------------------------------------
ALTER TABLE honor_enrollments
  ADD COLUMN IF NOT EXISTS program_id uuid REFERENCES programs(id);

DO $$
DECLARE offending bigint;
BEGIN
  SELECT count(*) INTO offending
    FROM honor_enrollments
   WHERE num_nonnulls(honor_id, program_id) <> 1;
  IF offending > 0 THEN
    RAISE EXCEPTION
      'No se puede aplicar 012_programs.sql: % inscripción(es) no tienen exactamente una de honor_id / program_id. Corrige esas filas antes de continuar.',
      offending;
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'honor_enrollments_one_curriculum_check'
  ) THEN
    ALTER TABLE honor_enrollments
      ADD CONSTRAINT honor_enrollments_one_curriculum_check
      CHECK (num_nonnulls(honor_id, program_id) = 1);
  END IF;
END
$$;

ALTER TABLE honor_enrollments ALTER COLUMN honor_id DROP NOT NULL;

-- Una inscripción viva por persona y programa, igual que la de especialidades, que NO se
-- toca (honor_enrollments_user_honor_active_key sigue tal cual).
CREATE UNIQUE INDEX IF NOT EXISTS honor_enrollments_user_program_active_key
  ON honor_enrollments (user_id, program_id)
  WHERE status <> 'WITHDRAWN' AND program_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_honor_enrollments_program_status
  ON honor_enrollments (program_id, status) WHERE program_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 4. Progreso: el tipo del requisito se copia al inscribirse (como `is_practical`),
--    la fila del catálogo de programas queda como gemela informativa de
--    `requirement_id` y `satisfied_by_enrollment_id` guarda QUÉ logro completó el
--    requisito (F2). Todas NULL o con valor por defecto: las filas existentes
--    quedan exactamente como estaban (kind = 'FREE', que es lo que eran).
-- ---------------------------------------------------------------------------
ALTER TABLE requirement_progress
  ADD COLUMN IF NOT EXISTS kind varchar(8) NOT NULL DEFAULT 'FREE';
ALTER TABLE requirement_progress
  ADD COLUMN IF NOT EXISTS program_requirement_id uuid
  REFERENCES program_requirements(id) ON DELETE SET NULL;
ALTER TABLE requirement_progress
  ADD COLUMN IF NOT EXISTS satisfied_by_enrollment_id uuid
  REFERENCES honor_enrollments(id) ON DELETE SET NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'requirement_progress_kind_check'
  ) THEN
    ALTER TABLE requirement_progress
      ADD CONSTRAINT requirement_progress_kind_check
      CHECK (kind IN ('FREE', 'HONOR', 'PROGRAM', 'HOURS'));
  END IF;
END
$$;

-- Regla 9: un mismo logro no completa dos requisitos de la misma inscripción.
CREATE UNIQUE INDEX IF NOT EXISTS requirement_progress_satisfied_by_key
  ON requirement_progress (enrollment_id, satisfied_by_enrollment_id)
  WHERE satisfied_by_enrollment_id IS NOT NULL;

-- `completed_via` admite tres orígenes automáticos más (F2). El CHECK se sustituye por uno
-- MÁS AMPLIO: ninguna fila existente deja de cumplirlo, porque 'REVIEW' y 'EXAM' siguen
-- dentro. 'COURSE' llegará con F9, en su propia migración.
ALTER TABLE requirement_progress DROP CONSTRAINT IF EXISTS requirement_progress_completed_via_check;
ALTER TABLE requirement_progress
  ADD CONSTRAINT requirement_progress_completed_via_check
  CHECK (completed_via IN ('REVIEW', 'EXAM', 'HONOR', 'PROGRAM', 'HOURS'));

-- ---------------------------------------------------------------------------
-- 5. Certificados de investidura. `honor_name_snapshot` guarda el nombre del
--    programa (es «lo que se obtuvo») y `honor_id` queda NULL: SÓLO los
--    certificados con `honor_id` habilitan la compra de parche en Sevenpxs, así
--    que un certificado de clase nunca se confunde con uno de especialidad.
--    La columna NO entra en el hash (app/services/certificates.py::canonical),
--    de modo que todos los certificados ya emitidos siguen verificando igual.
-- ---------------------------------------------------------------------------
ALTER TABLE certificates ADD COLUMN IF NOT EXISTS program_id uuid REFERENCES programs(id);
CREATE INDEX IF NOT EXISTS ix_certificates_program
  ON certificates (program_id) WHERE program_id IS NOT NULL;
