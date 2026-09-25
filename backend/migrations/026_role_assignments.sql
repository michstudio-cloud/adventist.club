-- 026_role_assignments.sql
-- Bloque I §1 — Varios roles por persona e invitaciones de organización.
-- Spec: docs/superpowers/specs/2026-09-24-eventos.md §1.1 y §1.2
--
-- Aditiva e idempotente: dos tablas nuevas, sus índices y el backfill de una asignación
-- ACTIVE por usuario. No borra, renombra ni reescribe nada: `users.role` y
-- `users.organization_id` siguen siendo lo que lee todo el código existente; desde ahora
-- son el ROL PRINCIPAL, que recalcula `app/services/role_assignments.py`.
-- Aplicar ANTES de desplegar el código que las mapea (el 025 queda para eventos).
-- Vuelta atrás: `DROP TABLE role_assignments; DROP TABLE org_invitations;`

-- ---------------------------------------------------------------------------
-- Invitaciones de organización. Siempre nominales (el correo es obligatorio) y de un
-- solo uso. El token vive SÓLO como SHA-256, igual que `club_invitations`.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS org_invitations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  role varchar(40) NOT NULL
    CHECK (role IN ('ADMIN_ASSOCIATION','COORDINATOR_ZONE','INSTRUCTOR','CLUB_DIRECTOR')),
  email citext NOT NULL,
  token_hash char(64) NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  accepted_at timestamptz,
  accepted_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  revoked_at timestamptz,
  revoked_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  -- ≤ 30 días (spec §1.2); el servicio pone el plazo, esto sólo impide uno mayor.
  CHECK (expires_at <= created_at + interval '30 days 1 minute')
);

CREATE INDEX IF NOT EXISTS ix_org_invitations_org ON org_invitations (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_org_invitations_email ON org_invitations (email);

-- ---------------------------------------------------------------------------
-- Asignaciones de rol. `organization_id` es el ámbito (asociación, zona, iglesia o club);
-- NULL sólo lo usa MASTER_GC… y las cuentas sin organización que el backfill copia tal
-- cual (un STUDENT suelto). Un rol sobre un nodo alcanza a todo su subárbol (`path`).
--
-- `is_primary` marca la fila que `users.role` + `users.organization_id` reflejan (el rol
-- principal). Mientras el código antiguo siga escribiendo esas dos columnas, esa fila puede
-- quedar atrás: `rbac.effective_roles` usa entonces las columnas de `users` y el servicio
-- la pone al día en su siguiente escritura.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS role_assignments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role varchar(40) NOT NULL,
  organization_id uuid REFERENCES organizations(id) ON DELETE CASCADE,
  status varchar(10) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','ENDED')),
  is_primary boolean NOT NULL DEFAULT false,
  -- BACKFILL | LEGACY (reflejo de users.role) | INVITATION | CLUB (espejo de club_memberships) | ADMIN
  source varchar(20) NOT NULL DEFAULT 'ADMIN',
  invitation_id uuid REFERENCES org_invitations(id) ON DELETE SET NULL,
  granted_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  granted_at timestamptz NOT NULL DEFAULT now(),
  ended_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  ended_at timestamptz,
  end_reason varchar(40),
  CHECK ((status = 'ENDED') = (ended_at IS NOT NULL))
);

-- Único activo por (usuario, rol, organización); NULL cuenta como un valor más.
CREATE UNIQUE INDEX IF NOT EXISTS ux_role_assignments_active
  ON role_assignments (user_id, role, COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid))
  WHERE status = 'ACTIVE';
-- Un solo rol principal vigente por persona.
CREATE UNIQUE INDEX IF NOT EXISTS ux_role_assignments_primary
  ON role_assignments (user_id) WHERE status = 'ACTIVE' AND is_primary;
CREATE INDEX IF NOT EXISTS ix_role_assignments_org
  ON role_assignments (organization_id) WHERE status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- Backfill: una asignación ACTIVE y principal por usuario, con su rol y organización de
-- hoy. Idempotente: sólo toca a quien todavía no tiene ninguna asignación.
-- ---------------------------------------------------------------------------
INSERT INTO role_assignments (user_id, role, organization_id, status, is_primary, source, granted_at)
SELECT u.id, u.role, u.organization_id, 'ACTIVE', true, 'BACKFILL', COALESCE(u.created_at, now())
FROM users u
WHERE u.role IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM role_assignments ra WHERE ra.user_id = u.id);

COMMENT ON TABLE role_assignments IS
  'Roles con ámbito de cada persona (bloque I §1.1). users.role = el de mayor ROLE_RANK (fila is_primary).';
COMMENT ON TABLE org_invitations IS
  'Invitaciones nominales de un solo uso a un rol de organización (bloque I §1.2). Token sólo como SHA-256.';
