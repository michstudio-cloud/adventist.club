-- 008b_club_membership.sql
-- Bloque E, incremento E2 — Núcleo de membresía de club.
-- Spec: docs/superpowers/specs/2026-09-22-membresia-de-club-design.md §3 y §5.3
--
-- Aditiva e idempotente: dos roles más en el CHECK de `users.role`, la tabla
-- `club_memberships` con sus índices y un relleno que sólo INSERTA lo que falta.
-- Ninguna columna ni fila existente se borra, renombra ni reescribe. El único
-- DROP es el `DROP CONSTRAINT IF EXISTS` del CHECK que este mismo archivo
-- vuelve a crear, con el mismo criterio que `001`.
-- Aplicar ANTES de desplegar el código que la mapea.

-- ---------------------------------------------------------------------------
-- Roles nuevos: Secretaría de club y consejero(a) de unidad. Ninguno es
-- administrativo ni se puede elegir al registrarse; los concede el club.
-- ---------------------------------------------------------------------------
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
  CHECK (role IN ('MASTER_GC','ADMIN_DIVISION','ADMIN_UNION','ADMIN_ASSOCIATION',
                  'COORDINATOR_ZONE','CLUB_DIRECTOR','CLUB_SECRETARY','INSTRUCTOR',
                  'COUNSELOR','STUDENT','PARENT_GUARDIAN'));

-- ---------------------------------------------------------------------------
-- El libro de la membresía: quién entró, cómo, quién lo aprobó y cuándo salió.
-- `users.organization_id` sigue siendo la verdad para el RBAC; esta tabla es el
-- historial y la única que explica por qué alguien está en un club.
-- `invitation_id` se queda sin FK hasta `008c`, que crea `club_invitations`;
-- `unit_id` lo añade `008d` (E5). Las dos columnas nacen NULL.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS club_memberships (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  club_id uuid NOT NULL REFERENCES organizations(id),
  role varchar(40) NOT NULL DEFAULT 'STUDENT'
    CHECK (role IN ('STUDENT','COUNSELOR','INSTRUCTOR','CLUB_SECRETARY','CLUB_DIRECTOR')),
  status varchar(20) NOT NULL
    CHECK (status IN ('PENDING_CONSENT','PENDING_APPROVAL','ACTIVE','ENDED','REJECTED','CANCELLED')),
  source varchar(12) NOT NULL
    CHECK (source IN ('INVITATION','REQUEST','ADMIN','FOUNDER','BACKFILL')),
  invitation_id uuid,
  message varchar(500),
  -- Sólo menores: a quién se le pidió el consentimiento para ESTE club.
  guardian_email citext,
  consent_token_hash char(64) UNIQUE,
  consent_expires_at timestamptz,
  consent_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  consent_at timestamptz,
  decided_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  decided_at timestamptz,
  decision_reason text CHECK (char_length(decision_reason) <= 1000),
  started_at timestamptz,
  ended_at timestamptz,
  end_reason varchar(20)
    CHECK (end_reason IN ('LEFT','REMOVED','TRANSFERRED','CONSENT_REVOKED','CLUB_CLOSED','DECLINED','EXPIRED')),
  ended_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Un club activo por persona (decisión D6): el traslado es terminar una y
-- activar otra en la misma transacción, nunca dos a la vez.
CREATE UNIQUE INDEX IF NOT EXISTS club_memberships_one_active_key
  ON club_memberships (user_id) WHERE status = 'ACTIVE';
-- Una sola solicitud viva por persona y club.
CREATE UNIQUE INDEX IF NOT EXISTS club_memberships_one_pending_key
  ON club_memberships (user_id, club_id)
  WHERE status IN ('PENDING_CONSENT','PENDING_APPROVAL');
CREATE INDEX IF NOT EXISTS ix_club_memberships_club_status ON club_memberships (club_id, status);

-- ---------------------------------------------------------------------------
-- Relleno no destructivo: una membresía ACTIVE por cada cuenta con rol de club
-- que hoy cuelga de un nodo `club`. Sólo INSERT ... WHERE NOT EXISTS, así que
-- volver a ejecutarlo no cambia nada y nunca toca filas ya escritas por la API.
--
-- El despliegue incluye REVISAR A MANO estas filas (hoy son un puñado): antes
-- de cerrar `POST /auth/register`, un INSTRUCTOR podía apuntarse solo a
-- cualquier club; quien lo hiciera se da de baja desde la nómina.
-- ---------------------------------------------------------------------------
INSERT INTO club_memberships (id, user_id, club_id, role, status, source, started_at, created_at, updated_at)
SELECT gen_random_uuid(), u.id, u.organization_id, u.role, 'ACTIVE', 'BACKFILL',
       COALESCE(u.created_at, now()), now(), now()
FROM users u
JOIN organizations o ON o.id = u.organization_id
WHERE o.type = 'club'
  AND u.role IN ('STUDENT','COUNSELOR','INSTRUCTOR','CLUB_SECRETARY','CLUB_DIRECTOR')
  AND NOT EXISTS (
    SELECT 1 FROM club_memberships m WHERE m.user_id = u.id AND m.status = 'ACTIVE'
  );
