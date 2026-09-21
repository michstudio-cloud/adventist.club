-- 008f_leader_verification.sql
-- Bloque E, incremento E7 — Carta de la iglesia para los cargos del club.
-- Spec: docs/superpowers/specs/2026-09-22-membresia-de-club-design.md §3 y §5.6
--       (adaptada: la tabla es la del bloque B, `church_letters`)
--
-- DECISIÓN TOMADA: hay UNA sola tabla de cartas en la plataforma. El bloque B
-- ya creó `church_letters` (migración 009) con el doble escalón zona ->
-- Asociación y el bucket privado. E7 la REUTILIZA ampliando `role_requested`
-- (`CLUB_DIRECTOR`, `COUNSELOR`, `CLUB_SECRETARY` además de `INSTRUCTOR`), que
-- es varchar(40) y se valida en el servicio: **no se crea `leader_verifications`**.
--
-- Esta migración añade UNA columna: la copia de la verificación vigente en
-- `users`, para que `rbac.is_verified_leader` sea una función pura y
-- `can_review` del bloque A no necesite otra consulta por dictamen.
--
-- Aditiva e idempotente: no borra, renombra ni reescribe nada.
-- Aplicar ANTES de desplegar el código.

-- Copia de la carta APROBADA vigente. La escribe SÓLO el servicio: al aprobar;
-- la borra al rechazar, al revocar, al cambiar de club y al salir del club.
-- NULL = sin verificar (que es el estado de todo el mundo hasta que alguien
-- valide una carta, y por eso el interruptor de despliegue nace apagado).
ALTER TABLE users ADD COLUMN IF NOT EXISTS leader_verified_until date;

-- El aviso de vencimiento (30 días antes) y el panel leen por esta columna.
CREATE INDEX IF NOT EXISTS ix_users_leader_verified_until
  ON users (leader_verified_until) WHERE leader_verified_until IS NOT NULL;

-- La cola de validación de E7 lee por estado y fecha de vigencia.
CREATE INDEX IF NOT EXISTS ix_church_letters_valid_until
  ON church_letters (valid_until) WHERE status = 'AUTHORIZED';
