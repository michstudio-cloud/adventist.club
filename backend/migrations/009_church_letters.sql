-- 009_church_letters.sql
-- Bloque B · I1 — La carta firmada por la iglesia que respalda un cargo, con su validación.
-- Spec: docs/superpowers/specs/2026-09-22-cursos-examenes-certificacion-design.md §3.1 (decisión D1)
--
-- Añade UNA tabla, `church_letters`: genérica por organización (no por club) y con el
-- doble escalón zona -> Asociación. Es la tabla que el bloque E (membresía) reutilizará
-- para directores y guías mayores ampliando `role_requested`, sin migrar de nuevo.
-- Aditiva e idempotente: sólo CREATE ... IF NOT EXISTS. No borra, renombra ni reescribe nada.
-- Aplicar ANTES de desplegar el código que la mapea.

-- ---------------------------------------------------------------------------
-- El archivo vive en el bucket PRIVADO de A (`storage_key`, nunca una URL):
-- es un dato personal con firma. `organization_id` es la organización del
-- usuario al presentarla y fija quién la revisa; no cambia después.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS church_letters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- Validado en el servicio contra LETTER_ROLES; E y F amplían la constante sin migrar.
  role_requested varchar(40) NOT NULL DEFAULT 'INSTRUCTOR',
  organization_id uuid NOT NULL REFERENCES organizations(id),
  church_name varchar(180) NOT NULL,
  pastor_name varchar(180),
  -- Para cuando el bloque E cargue las iglesias como organizaciones; hoy siempre NULL.
  church_org_id uuid REFERENCES organizations(id),
  storage_key text NOT NULL UNIQUE,
  content_type varchar(40) NOT NULL,
  size_bytes bigint NOT NULL CHECK (size_bytes > 0),
  status varchar(16) NOT NULL DEFAULT 'PENDING_UPLOAD'
    CHECK (status IN ('PENDING_UPLOAD','SUBMITTED','ZONE_VALIDATED','AUTHORIZED','REJECTED','REVOKED')),
  zone_validated_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  zone_validated_at timestamptz,
  -- Quien autorizó, rechazó o revocó.
  decided_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  decided_at timestamptz,
  decision_note text CHECK (char_length(decision_note) <= 2000),
  -- D7: la fija quien autoriza. NULL = sin caducidad.
  valid_until date,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Una carta viva por persona y cargo. Las rechazadas y revocadas son historia y no estorban.
CREATE UNIQUE INDEX IF NOT EXISTS church_letters_user_role_live_key
  ON church_letters (user_id, role_requested)
  WHERE status IN ('SUBMITTED','ZONE_VALIDATED','AUTHORIZED');

-- La cola de validación lee por organización y estado.
CREATE INDEX IF NOT EXISTS ix_church_letters_org_status ON church_letters (organization_id, status);
-- La lista de comprobación del interesado lee su carta más reciente.
CREATE INDEX IF NOT EXISTS ix_church_letters_user ON church_letters (user_id, created_at DESC);
