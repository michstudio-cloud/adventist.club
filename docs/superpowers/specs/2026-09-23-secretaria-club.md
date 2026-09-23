# Secretaría del club (bloque H)

Visión (VISION_ECOSISTEMA.md): «Secretaría: gestiona los oficiales del club, enlaces de invitación
por correo/WhatsApp, puntuación del club (gamificación)». Estado previo (2026-09-23): el rol
`CLUB_SECRETARY` existe (E8) y ya puede gestionar miembros STUDENT, unidades, invitaciones y datos
de contacto del club; puede otorgar puntos XP. **No** puede registrar asistencia, no hay cargos
(subdirector, tesorero, capellán…), no hay «pasar lista» por reunión, no hay exportación de nómina.

## 1. Cargos (oficiales del club)

- Tabla `club_officers` (migración `015_secretaria.sql`): `id, club_id, membership_id (único
  activo por cargo+persona), title` (`DIRECTOR | SUBDIRECTOR | SECRETARIO | TESORERO | CAPELLAN |
  CONSEJERO | INSTRUCTOR | OTRO`), `custom_title` (≤60, sólo con OTRO), `since` (date),
  `until` (date|null), `created_by_id, created_at`. Un cargo es un **título**, no un permiso: los
  permisos siguen viniendo de `club_memberships.role` / `users.role`.
- Quién gestiona: dirección y secretaría del club; MASTER/administración en alcance. Dirección
  puede nombrar cualquier cargo; secretaría todos menos DIRECTOR/SUBDIRECTOR.
- Endpoints: `GET /clubs/{id}/officers`, `POST /clubs/{id}/officers`, `PATCH …/{oid}` (until,
  custom_title), `DELETE …/{oid}` (cierra con `until = hoy`, no borra). Auditados.
- Se muestran en la nómina (píldora del cargo) y en la página del club.

## 2. Asistencia por reunión («pasar lista»)

- Tabla `club_meetings`: `id, club_id, held_on (date), kind` (`REUNION | CAMPAMENTO | SERVICIO |
  OTRO`), `title (≤120)`, `notes (≤500)`, `created_by_id, created_at`; única por club+fecha+kind+title.
- `POST /clubs/{id}/meetings` crea la reunión; `GET /clubs/{id}/meetings?from&to` lista con conteo.
- `PUT /clubs/{id}/meetings/{mid}/attendance` body `{entries: [{membership_id, status:
  PRESENT|ABSENT|JUSTIFIED}]}` (idempotente: reemplaza la lista de esa reunión). Por cada PRESENT
  se garantiza un `activity_logs` `ATTENDANCE` `APPROVED` (quantity 1, `performed_on = held_on`,
  `meeting_id` nuevo en `activity_logs`), y se elimina si deja de estar presente. XP: +5 por
  asistencia (ya definido).
- Quién: dirección, **secretaría** y consejero de unidad (sólo su unidad). `rbac.can_record_attendance`.
- `GET /clubs/{id}/meetings/{mid}` devuelve la lista con el estado de cada miembro activo.
- Resumen: `GET /clubs/{id}/attendance/summary?from&to` → por miembro: reuniones, presentes, % ;
  por unidad: %.

## 3. Nómina y datos

- `GET /clubs/{id}/members` gana `officer_titles: string[]`, `completeness: {birth_date: bool,
  consent: bool, guardian: bool, email_verified: bool}` (flags, nunca datos), `attendance_pct_90d`.
- `GET /clubs/{id}/members/export.csv` (dirección y secretaría): nombre, cargo, rol, unidad,
  edad (años), estado, fecha de ingreso, asistencia 90 d. **Sin** correo ni tutor para la
  secretaría; la dirección recibe además correo del miembro adulto y nombre del tutor. Auditado
  (`EXPORT`/`CLUB_ROSTER`).

## 4. Invitaciones

- La UI permite invitar `CLUB_SECRETARY` (uso único, por correo) y elegir **unidad** en el enlace.
- Código QR del enlace multiuso (frontend, sin dependencia externa: `qrcode` ya está en el proyecto).
- Enlace multiuso «del club»: se mantiene el máximo de 90 días; la secretaría puede **renovarlo**
  (`POST …/invitations/{iid}/renew` → nuevo enlace con las mismas reglas, revoca el anterior).

## 5. Página del club y perfil

- `GET /clubs/{id}/profile` (público si el club está activo): nombre, iglesia, zona, asociación,
  ciudad, `meeting_day`, `meeting_time`, `contact`, `description (≤600, nuevo)`, oficiales
  (cargo + nombre, sin correo), nº de miembros activos, `accepts_requests`, `logo_url` (nuevo,
  carpeta `logos`, sólo dirección/secretaría).
- Frontend `/clubs/[id]`: página pública del club con «Solicitar unirme» (ya existe la hoja).

## 6. Puntuación del club

- `GET /clubs/{id}/score?season=YYYY` → total de XP del club en la temporada (suma de puntos
  positivos de sus miembros + 5 × asistencias + insignias), por unidad y por mes. Sólo para el
  club y su jerarquía; comparación entre clubes queda para la asociación (bloque posterior).

## Fuera de alcance
Tesorería (cuotas/pagos), mensajería, secretaría de asociación.
