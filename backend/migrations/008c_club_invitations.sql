-- 008c_club_invitations.sql
-- Bloque E, incremento E3 — Invitaciones al club y consentimiento del tutor.
-- Spec: docs/superpowers/specs/2026-09-22-membresia-de-club-design.md §3, §5.1 y §5.9
--
-- Aditiva e idempotente: dos tablas nuevas, sus índices y la clave ajena que
-- `008b` dejó pendiente en `club_memberships.invitation_id`. No borra, renombra
-- ni reescribe nada. Aplicar ANTES de desplegar el código que la mapea.

-- ---------------------------------------------------------------------------
-- El enlace que el club comparte. El token vive SÓLO como SHA-256: quien mira
-- la base no puede entrar con él, y se muestra una única vez a quien lo crea.
-- `max_uses = 1` (el valor por defecto) activa directo; un enlace multiuso
-- —el del grupo de WhatsApp o el QR de la reunión— sólo admite STUDENT y deja
-- a quien lo usa esperando la confirmación del director (decisión D2).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS club_invitations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  club_id uuid NOT NULL REFERENCES organizations(id),
  created_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  role varchar(40) NOT NULL
    CHECK (role IN ('STUDENT','COUNSELOR','INSTRUCTOR','CLUB_SECRETARY')),
  -- FK a club_units en 008d (E5); nace NULL.
  unit_id uuid,
  -- Invitación nominal: sólo la acepta la cuenta con este correo.
  email citext,
  token_hash char(64) NOT NULL UNIQUE,
  max_uses integer NOT NULL DEFAULT 1 CHECK (max_uses BETWEEN 1 AND 200),
  uses integer NOT NULL DEFAULT 0 CHECK (uses >= 0),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  revoked_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_club_invitations_club ON club_invitations (club_id, expires_at);

-- La membresía recuerda por qué enlace entró alguien.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'club_memberships_invitation_id_fkey'
  ) THEN
    ALTER TABLE club_memberships
      ADD CONSTRAINT club_memberships_invitation_id_fkey
      FOREIGN KEY (invitation_id) REFERENCES club_invitations(id) ON DELETE SET NULL;
  END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Constancia de envío y antirrepetición de los correos transaccionales. NUNCA
-- guarda el cuerpo del mensaje: sólo a qué dirección salió qué clase de aviso
-- y sobre qué fila, para no repetirlo y para poder explicar qué se envió.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  email citext NOT NULL,
  kind varchar(40) NOT NULL,
  entity_type varchar(40) NOT NULL,
  entity_id text NOT NULL,
  sent_at timestamptz NOT NULL DEFAULT now(),
  ok boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS ix_notification_log_kind_entity
  ON notification_log (kind, entity_id, sent_at DESC);
