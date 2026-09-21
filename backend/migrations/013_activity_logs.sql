-- 013_activity_logs.sql
-- Bloque F · F2 — Actividades del miembro: horas de servicio y asistencia a reuniones,
-- registradas por el propio miembro o por el director, y aprobadas por el director. Son la
-- ÚNICA vía por la que se completa un requisito de tipo `HOURS`.
-- Spec: docs/superpowers/specs/2026-09-22-guias-mayores-aventureros-design.md §1.3
--
-- QUÉ CAMBIA: crea UNA tabla nueva, `activity_logs`, y nada más. No toca ninguna tabla
-- existente, no borra, no renombra y no reescribe ninguna fila. El resto de F2 (requisitos
-- `HONOR` y `PROGRAM` que se completan solos, hueco de especialidad a elección del miembro)
-- usa columnas que ya creó 012_programs.sql.
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, y DESPUÉS de 012_programs.sql.
-- Idempotente. Vuelta atrás: `DROP TABLE activity_logs` mientras ningún requisito `HOURS`
-- esté completado (`requirement_progress.completed_via = 'HOURS'`); si los hay, esos
-- requisitos volverían a PENDING al recalcularse y hay que decidirlo a mano.

CREATE TABLE IF NOT EXISTS activity_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  -- De quién son las horas. En cascada: borrar la cuenta borra su registro.
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- El club del miembro cuando se registró: quién tiene que aprobarlas. Puede quedar NULL
  -- si el club desaparece; entonces sólo Zona, Asociación o MASTER pueden decidirlas.
  club_id uuid REFERENCES organizations(id) ON DELETE SET NULL,
  category varchar(12) NOT NULL CHECK (category IN ('SERVICE', 'ATTENDANCE')),
  performed_on date NOT NULL,
  -- Horas (≤ 24 en un día) o reuniones (1). Nunca cero ni negativo.
  quantity numeric(4,1) NOT NULL CHECK (quantity > 0 AND quantity <= 24),
  description varchar(500) NOT NULL,
  place varchar(180),
  status varchar(10) NOT NULL DEFAULT 'SUBMITTED'
    CHECK (status IN ('SUBMITTED', 'APPROVED', 'REJECTED')),
  -- Quién la registró: el propio miembro, o el director que registró la salida del club.
  created_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  decided_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  decided_at timestamptz,
  decision_note varchar(500),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  -- Una decisión deja siempre constancia de quién y cuándo.
  CONSTRAINT activity_logs_decision_check
    CHECK ((status = 'SUBMITTED') = (decided_at IS NULL))
);

-- Lo que suma para un requisito `HOURS` (por miembro, categoría y estado)…
CREATE INDEX IF NOT EXISTS ix_activity_logs_user_category
  ON activity_logs (user_id, category, status);
-- …y la cola de «Horas por aprobar» del club.
CREATE INDEX IF NOT EXISTS ix_activity_logs_club_status
  ON activity_logs (club_id, status);
