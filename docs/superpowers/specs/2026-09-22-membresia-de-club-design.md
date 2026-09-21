# Bloque E — Membresía de club: invitaciones, solicitudes, unidades, zona e iglesia, carta de la iglesia, Secretaría y 2FA

Fecha: 22 sep 2026 · Estado: **borrador para revisión del responsable** (el resto del documento asume las
recomendaciones de la sección 0; cambiar una decisión sólo toca la parte que se indica en ella).
Repos: `adventist.club` (FastAPI, este) y `conquistadores.app` (Next.js). Todo es aditivo.
Mapa A→F y decisiones del bloque A: `docs/superpowers/specs/2026-09-22-portafolio-design.md`.

E es independiente de A, pero A depende de su resultado: en A dictaminan «el director y los instructores del
**club actual** del miembro». Este bloque define cómo alguien llega a ser miembro, instructor o director *de
verdad* de un club, y qué puede hacer mientras no esté verificado.

## 0. Decisiones para el responsable

Cada una trae opciones, recomendación y consecuencia. **El documento está escrito con la opción recomendada.**

| # | Decisión | Opciones | Recomendación | Consecuencia |
|---|---|---|---|---|
| D1 | ¿Qué es «Secretaría»? La visión la describe a nivel **Asociación** (elige clubes, reasigna cargos, puntajes); el mapa de E la pide a nivel **club** | a) sólo de club · b) sólo de asociación · c) las dos, en dos pasos | **c)** ahora `CLUB_SECRETARY` (de un club, sin dictamen); la Secretaría de Asociación llega con el bloque de puntajes | Hoy el relevo de director lo sigue haciendo un administrador con `PATCH /users/{id}` (ya existe). Si se elige b), sobran §5.7 y el incremento E8 |
| D2 | Un enlace **multiuso** (el que se comparte en el grupo de WhatsApp o por QR en la reunión), ¿activa al instante? | a) activa directo · b) entra como «por confirmar» y el director aprueba (uno a uno o todos) · c) no hay multiuso | **b)**; el enlace de **un solo uso** sí activa directo | Un enlace filtrado no mete desconocidos a un club con menores ni llena la cola de dictamen de A. Coste: un toque del director por tanda |
| D3 | Al registrar un club, la zona o la iglesia **todavía no existen** en el árbol (hoy sólo están cargadas divisiones, uniones y asociaciones) | a) bloquear hasta que la asociación las cargue · b) el director elige de la lista o **propone el nombre**; quien aprueba el club lo ubica (elige o crea los nodos) · c) el director crea los nodos | **b)** | Nadie queda bloqueado el primer día y ningún club queda **activo** sin zona e iglesia. La estructura eclesiástica sólo la crean administradores (principio 7 de la visión) |
| D4 | ¿La carta de la iglesia bloquea al **director** desde el primer día? | a) sí, igual que al instructor · b) **gracia de 60 días** desde la aprobación del club; instructores y consejeros sin gracia · c) la carta sólo se exige a instructores | **b)** | Con a) ningún club podría dictaminar a menores hasta que exista y actúe un coordinador de zona: el bloque A nacería parado. Con b) la aprobación del club (un acto humano de la asociación) cubre los primeros 60 días |
| D5 | Vigencia de la carta validada | a) 12 meses · b) 24 meses · c) sin vencimiento | **a)** con aviso 30 días antes y renovación desde 60 días antes | Los cargos de iglesia se nombran por periodo; al vencer, el líder vuelve a «sin verificar» (no dictamina a menores) hasta renovar |
| D6 | ¿Una persona puede pertenecer a varios clubes a la vez? | a) un club activo a la vez · b) varios | **a)** | A usa `users.organization_id` (un solo club) para la jurisdicción. Cambiar de club es un **traslado**. b) obligaría a rehacer `can_review` de A |
| D7 | ¿El **consejero** de unidad dictamina evidencias? | a) no: ve a su unidad y su avance, no dictamina · b) sí, sólo de su unidad | **a)**; si debe dictaminar, el director le da el rol de instructor (puede seguir siendo consejero de una unidad) | `can_review` de A no cambia de forma. b) añadiría una tercera rama de jurisdicción por unidad |
| D8 | Menores **sin correo propio** (10–12 años) | a) fuera de E: toda cuenta necesita un correo (puede ser un alias del tutor, `mama+ana@…`) · b) cuentas de menor creadas y gestionadas por el tutor, sin correo | **a)** | b) exige acceso sin correo (usuario o código), recuperación de contraseña por el tutor y otra pantalla de alta: es un bloque propio. Se deja anotado como riesgo de adopción |
| D9 | ¿Puede ser tutor en la plataforma un adulto con otro rol (el director cuyo hijo es conquistador)? Hoy sólo `PARENT_GUARDIAN` puede | a) cualquier adulto con correo verificado · b) sólo `PARENT_GUARDIAN` (el director necesitaría una segunda cuenta) | **a)** | Se relaja una comprobación en `routers/users.py`. `PARENT_GUARDIAN` queda como el rol de quien no tiene otra función |

## 1. Lo que hay hoy y lo que E tiene que corregir

Pertenecer a un club hoy **es** `users.organization_id` + `users.role`; no hay invitaciones, solicitudes, historial
ni unidades. El director registra su club (`services/clubs.py`), que cuelga **directo de la asociación** con la
iglesia como texto libre en `metadata_json.church`; lo aprueba un administrador con alcance (`can_decide_club`).

Hallazgos al leer el código, todos dentro del alcance de E porque A se apoya en ellos:

1. **`POST /auth/register` acepta `organization_id` de cualquiera.** Una cuenta nueva con rol `INSTRUCTOR` puede
   apuntarse a cualquier club y, con A desplegado, entrar en `can_review` de sus miembros. E lo cierra: al club se
   entra sólo por invitación, solicitud aprobada o acto de un administrador (incremento E2).
2. **`GET /users` no comprueba rol**: cualquier cuenta con `organization_id` lista a todas las personas de su
   subárbol, con correo y fecha de nacimiento. Hoy casi nadie tiene club; en cuanto E meta conquistadores en clubes
   sería una fuga de datos de menores. E2 lo limita a `MEMBER_VIEW_ROLES`.
3. `rbac.club_scope_paths` da al coordinador de zona **toda la asociación** porque zona y club son hermanos. Al
   colgar el club de su iglesia (zona → iglesia → club) el alcance sale del árbol y el atajo se retira (E6).
4. `org.py` supone que **el padre del club es la asociación** (`nearby_clubs`, `list_pending_clubs`, `_decide_club`,
   y en el frontend `panel/page.tsx` con `org.parent_id`). Con iglesia y zona hay que buscar el **ancestro** de tipo
   `association` (E6).
5. `users.verification_status` ya significa «correo / cuenta verificada». **No se reutiliza** para la carta de la
   iglesia: son dos cosas distintas y tienen dueños distintos.
6. Tutelas: en el código `guardianships.consent_status` vale `PENDING | APPROVED | REJECTED`. La spec de A escribe
   `'GRANTED'`; **manda el código**: en todo este documento «tutor con consentimiento» significa `APPROVED`.
7. 2FA: `mfa_disable` ya impide a `MASTER_GC` desactivarlo y `PATCH /users` impide ascender a `MASTER_GC` sin MFA,
   pero una cuenta `MASTER_GC` sembrada sin MFA entra y opera, y el token no dice si nació de un segundo factor.
8. `ORG_HIERARCHY` ya incluye `zone`, `church`, `club`, `unit`, y `POST /org-nodes` ya valida nivel y alcance: crear
   zonas e iglesias **no necesita endpoint nuevo**.

## 2. Principios del bloque

1. **`users.organization_id` sigue siendo la verdad para el RBAC** (lo leen `rbac.py` y A). `club_memberships` es el
   libro: quién entró, cómo, quién lo aprobó, cuándo salió. **Un único servicio** (`app/services/memberships.py`)
   escribe las dos cosas en la misma transacción; ningún router toca `organization_id` de una cuenta de club por su
   cuenta (incluido `PATCH /users/{id}`, que pasa a delegar en él).
2. Un club activo por persona (D6). Las cuentas administrativas (`ADMIN_*`, `COORDINATOR_ZONE`, `MASTER_GC`) no
   tienen membresía: su `organization_id` es su jurisdicción.
3. Los roles se conceden hacia abajo con `outranks` (ya existe): nadie concede un rol igual o superior al suyo.
4. Un menor no está **activo** en un club sin el consentimiento de un tutor para **ese** club.
5. Los secretos (token de invitación, de consentimiento, códigos de recuperación) se guardan **sólo como hash
   SHA-256**, se aceptan **sólo en el cuerpo JSON** (regla ya vigente en `auth.py`) y se muestran una vez.
6. YAGNI: sin chat, sin cupo de club, sin listas de espera, sin multi-club, sin WhatsApp API (sólo se genera el
   enlace `wa.me` con texto, como pide la visión).

## 3. Modelo de datos — migraciones `008…008g` (aditivas, idempotentes, una por incremento)

`007` es A; `009+` queda reservado a cursos. Todas se pueden aplicar por adelantado y en orden: ninguna depende de
código. El único `DROP` es `DROP CONSTRAINT IF EXISTS` de un CHECK que el mismo archivo vuelve a crear (mismo
criterio que `001`).

### `008_mfa_recovery.sql` (E1)
`mfa_recovery_codes`: `id` uuid PK · `user_id` FK users ON DELETE CASCADE · `code_hash` char(64) UNIQUE ·
`used_at` timestamptz NULL · `created_at`. Índice `(user_id) WHERE used_at IS NULL`.

### `008b_club_membership.sql` (E2)
**Roles nuevos** en `users.role` (re-crea `users_role_check`): `CLUB_SECRETARY` y `COUNSELOR`. En
`app/security.py`: constantes, `ALL_ROLES`, `ROLE_RANK` (`CLUB_SECRETARY: 45`, `COUNSELOR: 30`) y
`CLUB_LEVEL_ROLES = (CLUB_DIRECTOR, CLUB_SECRETARY, INSTRUCTOR, COUNSELOR, STUDENT)`. **No** entran en `ADMIN_ROLES`
ni en `SELF_REGISTRATION_ROLES`. `RoleName` (`schemas/auth.py`) y `ROLE_LABELS` del frontend los incorporan
(«Secretaría de club», «Consejero(a) de unidad»).

**`club_memberships`**
| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK users ON DELETE CASCADE, NOT NULL | |
| club_id | uuid FK organizations, NOT NULL | nodo de tipo `club` |
| role | varchar(40) NOT NULL DEFAULT 'STUDENT' | rol que se concede al activarse: `STUDENT` \| `COUNSELOR` \| `INSTRUCTOR` \| `CLUB_SECRETARY` \| `CLUB_DIRECTOR` (mismo vocabulario que `users.role`) |
| status | varchar(20) NOT NULL | `PENDING_CONSENT` → `PENDING_APPROVAL` → `ACTIVE` → `ENDED`; `REJECTED`; `CANCELLED` |
| source | varchar(12) NOT NULL | `INVITATION` \| `REQUEST` \| `ADMIN` \| `FOUNDER` \| `BACKFILL` |
| invitation_id | uuid NULL | FK a `club_invitations` (se añade en `008c`) |
| message | varchar(500) NULL | nota de quien solicita |
| guardian_email | citext NULL | sólo menores: a quién se pidió el consentimiento |
| consent_token_hash | char(64) NULL UNIQUE | |
| consent_expires_at, consent_by_id (FK users), consent_at | | quién autorizó y cuándo |
| decided_by_id (FK users), decided_at, decision_reason (text ≤ 1000) | | aprobación o rechazo del club |
| started_at, ended_at | timestamptz NULL | |
| end_reason | varchar(20) NULL | `LEFT` \| `REMOVED` \| `TRANSFERRED` \| `CONSENT_REVOKED` \| `CLUB_CLOSED` \| `DECLINED` \| `EXPIRED` |
| ended_by_id | uuid FK users NULL | |
| created_at, updated_at | timestamptz | |

Índices: único parcial `(user_id) WHERE status = 'ACTIVE'` (D6); único parcial `(user_id, club_id) WHERE status IN
('PENDING_CONSENT','PENDING_APPROVAL')`; `(club_id, status)`.
**Relleno** (sólo `INSERT … WHERE NOT EXISTS`, no toca filas existentes): una membresía `ACTIVE`, `source =
'BACKFILL'`, por cada usuario con rol de club cuyo `organization_id` es un nodo `club`. El despliegue incluye
revisar a mano esas filas (hoy son un puñado): un `INSTRUCTOR` que se apuntó solo (hallazgo 1) se da de baja.

### `008c_club_invitations.sql` (E3)
**`club_invitations`**: `id` · `club_id` FK organizations · `created_by_id` FK users · `role` varchar(40) ·
`unit_id` uuid NULL (FK en `008d`) · `email` citext NULL (invitación nominal) · `token_hash` char(64) UNIQUE ·
`max_uses` int NOT NULL DEFAULT 1 CHECK (1..200) · `uses` int NOT NULL DEFAULT 0 · `expires_at` timestamptz NOT
NULL · `revoked_at`, `revoked_by_id` · `created_at`. Índice `(club_id, expires_at)`. Añade la FK
`club_memberships.invitation_id`.
**`notification_log`** (antirrepetición y constancia de envío): `id` · `user_id` FK users NULL · `email` citext ·
`kind` varchar(40) · `entity_type` varchar(40) · `entity_id` text · `sent_at` timestamptz · `ok` boolean. Índice
`(kind, entity_id, sent_at DESC)`. Nunca guarda el cuerpo del correo.

### `008d_club_units.sql` (E5)
**`club_units`**: `id` · `club_id` FK organizations NOT NULL · `name` varchar(80) · `min_age`, `max_age` smallint
NULL (CHECK `min_age <= max_age`; `max_age` NULL = sin tope) · `capacity` smallint NULL CHECK (1..50) ·
`counselor_id` FK users NULL ON DELETE SET NULL · `status` varchar(10) DEFAULT 'active' (`active` \| `archived`) ·
`created_at`, `updated_at`. Único parcial `(club_id, lower(name)) WHERE status = 'active'`.
Añade `club_memberships.unit_id` y la FK de `club_invitations.unit_id` (ambas NULL, ON DELETE SET NULL).

**Por qué una tabla ligera y no nodos `organizations.type = 'unit'`** (aunque el tipo ya exista):
- Si el miembro colgara de la unidad, `users.organization_id` dejaría de ser el club y se rompería `can_review` de A
  («cuya `organization_id` es el club actual del miembro») y todo lo que compara por igualdad con el club.
- La unidad no tiene jurisdicción propia: nadie administra «su subárbol»; el consejero no es un rol administrativo.
- Las lecturas de `org-nodes` son **públicas**: las unidades (grupos de menores con nombre) no deben salir ahí.
- Se reorganizan cada año; archivar una fila es trivial, reescribir `path` de un subárbol no.
El tipo `unit` se queda en `ORG_HIERARCHY` sin uso; no se borra nada.

### `008e_club_placement.sql` (E6)
Zona («zona / distrito») e iglesia **son nodos** `organizations` (`type = 'zone'`, `'church'`): de su `path` sale el
alcance del coordinador de zona con el RBAC que ya existe. La etiqueta «distrito» va en
`metadata_json.kind = 'district'`; no hay tipo nuevo. Sólo índices:
`ix_organizations_parent_type (parent_id, type)` y único parcial `(parent_id, lower(name)) WHERE type IN
('zone','church') AND status = 'active'` (antes de aplicarlo, comprobar que no hay duplicados; hoy no hay zonas).

### `008f_leader_verification.sql` (E7)
**`leader_verifications`** (una fila por carta presentada; el historial se conserva)
| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK users ON DELETE CASCADE | |
| club_id | uuid FK organizations | club para el que se presenta; la verificación **vale sólo en ese club** |
| role | varchar(40) | rol del solicitante al presentarla |
| status | varchar(15) | `PENDING_UPLOAD` → `SUBMITTED` → `APPROVED` \| `REJECTED`; `APPROVED` → `REVOKED`. «Vencida» se deriva de `valid_until` |
| storage_key | text NOT NULL UNIQUE | bucket **privado**: `leader-letters/<user_id>/<id>.<ext>`; nunca una URL |
| content_type varchar(40), size_bytes bigint, sha256 char(64) NULL | | |
| issued_on | date | fecha de la carta |
| signer_name varchar(180), signer_position varchar(120) NULL, church_name varchar(180) | | quién firma; copia del nombre de la iglesia |
| submitted_at, decided_by_id (FK users), decided_at, decision_note (text ≤ 1000) | | nota obligatoria al rechazar o revocar |
| valid_until | date NULL | |
| created_at | timestamptz | |

Único parcial `(user_id) WHERE status IN ('PENDING_UPLOAD','SUBMITTED')`. Índice `(club_id, status)`.
En `users`: **`leader_verified_until date NULL`** (copia de la verificación vigente; la escribe sólo el servicio: al
aprobar; la borra al revocar, al cambiar de club o al salir). Así `is_verified_leader(user)` es una función pura y
`can_review` de A no necesita otra consulta.

### `008g_notify_progress.sql` (E9)
`users.notify_progress boolean NOT NULL DEFAULT true`: permite apagar los correos de avance (dictámenes). Los de
seguridad, invitación, consentimiento y decisión no se pueden apagar.

## 4. Permisos — se amplía `app/rbac.py`, no se duplica nada

| Función | Regla |
|---|---|
| `can_manage_members(db, actor, club)` | `MASTER_GC`; administrador con el club en su alcance (`org_in_user_scope`); `CLUB_DIRECTOR` no bloqueado (`director_blocked`) o `CLUB_SECRETARY` con `organization_id == club.id`. El club debe estar `active` |
| `can_grant_club_role(actor, role)` | `outranks(actor, role)` **y**: los roles de personal (`COUNSELOR`, `INSTRUCTOR`, `CLUB_SECRETARY`) sólo los concede el director o un administrador; la Secretaría sólo gestiona `STUDENT`. `CLUB_DIRECTOR` no se concede por este camino (relevo: administrador con `PATCH /users/{id}`) |
| `can_view_roster(db, actor, club)` | quien cumple `can_manage_members`; `INSTRUCTOR` del club (nombre, unidad y rol); `COUNSELOR` (sólo sus unidades). Para `INSTRUCTOR` y `COUNSELOR` las filas de **menores** sólo se incluyen si cumplen `may_handle_minors` |
| `is_verified_leader(user, today)` | `user.child_protection_completed` y `leader_verified_until >= today`. Pura, sin consulta |
| `director_in_grace(user, today)` | `CLUB_DIRECTOR` no bloqueado y `today <= max(club_approval_at o created_at, LEADER_VERIFICATION_ENFORCED_FROM) + 60 días` (D4) |
| `may_handle_minors(user, today)` | `is_master` o `is_verified_leader` o `director_in_grace`. Con `LEADER_VERIFICATION_ENFORCED_FROM` sin valor ⇒ siempre cierto (interruptor de despliegue). **Es la única puerta que usan los demás permisos**; `is_verified_leader` a secas sólo decide el distintivo |
| `can_validate_leader(db, actor, verification)` | `is_admin_role(actor)`, `actor.id != verification.user_id` y `org_in_user_scope(db, actor, verification.club_id)`: el coordinador de **esa** zona o, si el club aún no está ubicado o la zona no tiene coordinador, la administración de la asociación |
| `club_scope_paths` / `can_decide_club` (cambian en E6) | El coordinador de zona decide sobre **su subárbol** y, además, sobre clubes que aún cuelgan directo de su asociación y no proponen una zona existente distinta de la suya; sólo puede ubicarlos en **su** zona y no crea zonas. Administración de asociación o superior: como hoy |

Cambios en funciones existentes (una línea cada uno, con test):
- `MEMBER_VIEW_ROLES` **no** incorpora `CLUB_SECRETARY` ni `COUNSELOR`: `GET /users/{id}` devuelve correo y fecha de
  nacimiento. La Secretaría lee la nómina por su propio endpoint, con un serializador recortado.
- `can_view_user`: si el objetivo es menor y el actor es `CLUB_DIRECTOR` o `INSTRUCTOR`, exige
  `may_handle_minors`. Nueva rama: el `COUNSELOR` (o cualquier adulto asignado como consejero) ve a los miembros
  `ACTIVE` de sus unidades; sólo se le puede asignar si cumple `may_handle_minors`.
- `can_review`, `can_issue` y la rama «revisor con jurisdicción» de `can_view_portfolio` (A): si el inscrito es menor,
  exigen además `may_handle_minors(actor)`. Se añade **dentro** de esas funciones, que es donde A concentra la
  jurisdicción. `CLUB_SECRETARY` y `COUNSELOR` no aparecen en ellas: no dictaminan ni certifican por construcción.
- `GET /users` (listado): sólo `MEMBER_VIEW_ROLES`; los demás se ven a sí mismos (hallazgo 2).

**Lo que puede y no puede un instructor (o director fuera de gracia) sin verificar**
| Puede | No puede |
|---|---|
| Pertenecer al club y aparecer en la nómina como «sin verificar» | Dictaminar o certificar inscripciones de **menores** |
| Proponer especialidades (flujo actual de `honors`) | Ver portafolio, evidencias o datos (`GET /users/{id}`) de menores |
| Llevar su propio portafolio | Ser consejero de una unidad |
| Dictaminar inscripciones de miembros **adultos** | Mostrar el distintivo «Instructor activo verificado» |
| Ver unidades y personal adulto del club | |

## 5. Flujos y API

Convenciones: todo autenticado salvo que se diga; `detail` en español como en A; los routers validan, llaman al
servicio y serializan; los correos salen en `BackgroundTasks` **después** del commit y nunca hacen fallar la
petición (patrón de `org.py`). Routers nuevos: `app/routers/memberships.py` (`/api/v1/memberships`, lo que hace una
persona sobre sí misma), `app/routers/clubs.py` (`/api/v1/clubs/{club_id}`, gestión del club) y
`app/routers/leader_verifications.py`. Servicios: `memberships.py`, `invitations.py`, `units.py`,
`placement.py`, `leader_verification.py`, `notifications.py`. Esquemas en `app/schemas/membership.py`.

### 5.1 Invitaciones (E3)

- **Un solo uso** (`max_uses = 1`, por defecto): vence a los 7 días. Al aceptarla, un adulto queda `ACTIVE`.
- **Multiuso** (`max_uses` 2–200): **sólo rol `STUDENT`**, vence a los 30 días, y quien la usa queda
  `PENDING_APPROVAL` (D2).
- **Roles de personal** (`COUNSELOR`, `INSTRUCTOR`, `CLUB_SECRETARY`): siempre un solo uso y **nominal** (`email`
  obligatorio); sólo la acepta la cuenta con ese correo.
- Vencimiento configurable hasta 90 días; máximo 20 invitaciones vigentes por club; revocables. Estado calculado:
  `ACTIVE` / `EXPIRED` / `REVOKED` / `EXHAUSTED`.
- El token (`generate_url_token()`, ya existe) se devuelve **una vez** junto con `url =
  {FRONTEND_URL}/join?t=<token>` y `whatsapp_url = https://wa.me/?text=<texto + url>`. El QR lo dibuja el frontend
  (ya usa `qrcode`). Si se pierde, se crea otra.
- Consumo atómico: `UPDATE club_invitations SET uses = uses + 1 WHERE id = :id AND uses < max_uses AND revoked_at
  IS NULL AND expires_at > now() RETURNING id` (mismo patrón que `verification._claim`).

| Método y ruta | Quién | Qué · errores |
|---|---|---|
| `POST /clubs/{club_id}/invitations` `{role, max_uses?, expires_in_days?, email?, unit_id?}` | `can_manage_members` + `can_grant_club_role` | 201 `{invitation, token, url, whatsapp_url}`; con `email` envía el correo. 403 rol no permitido; 409 club no activo o 20 vigentes; 422 multiuso con rol ≠ `STUDENT`, personal sin `email` |
| `GET /clubs/{club_id}/invitations` | `can_manage_members` | Lista sin tokens: rol, usos/máximo, vencimiento, estado, quién la creó |
| `DELETE /clubs/{club_id}/invitations/{id}` | `can_manage_members` (Secretaría: sólo las de `STUDENT`) | Revoca; idempotente |
| `POST /memberships/invitations/preview` `{token}` | **público**, 10/min por IP | `{club: {id, name, city, church}, role, requires_approval, expires_at}`. **Un único 404** «Invitación no válida o vencida» para inexistente, vencida, revocada o agotada (sin oráculo). Nunca nombres de personas |
| `POST /memberships/invitations/accept` `{token, guardian_email?, confirm_transfer?}` | autenticado | Crea la membresía (estado según la tabla de §5.9). 403 correo distinto al de una invitación nominal; 409 ya es miembro de ese club, tiene un rol administrativo, es director con club propio, o pertenece a otro club y falta `confirm_transfer`; 422 menor sin `guardian_email` ni tutor aprobado; 400 menor con rol ≠ `STUDENT` |
| `POST /auth/register` + `invitation_token`, `guardian_email` | público | Registro y aceptación en **una transacción** (igual que hoy con `club`); token inválido ⇒ 400 y no se crea la cuenta. `invitation_token` y `club` son excluyentes. **`organization_id` deja de aceptarse** (400 «Únete con una invitación o solicita unirte a un club») |

### 5.2 Solicitud de ingreso desde `/clubs` (E4)

Requiere correo verificado (`verification_status = 'VERIFIED'`). Máximo 3 solicitudes abiertas por persona, una por
club; tras un rechazo, 30 días antes de volver a pedir el mismo club. El club puede cerrar solicitudes
(`metadata_json.profile.accepts_requests = false`; `NearbyClub` expone `accepts_requests`). El rol pedido es
`STUDENT`, salvo que la cuenta sea `INSTRUCTOR` adulto, que pide `INSTRUCTOR`; al aprobar, el director puede
conceder otro rol permitido por `can_grant_club_role`. Las pendientes con más de 30 días se dan por `CANCELLED`
(`EXPIRED`) al leerlas: no hay tarea programada.

| Método y ruta | Quién | Qué · errores |
|---|---|---|
| `POST /memberships/requests` `{club_id, message?, guardian_email?}` | autenticado, correo verificado | 201 membresía `PENDING_APPROVAL` (adulto) o `PENDING_CONSENT` (menor). 404 club no activo; 409 solicitudes cerradas, duplicada, límite o enfriamiento; 403 rol administrativo |
| `DELETE /memberships/requests/{membership_id}` | quien la hizo | `CANCELLED` |
| `GET /clubs/{club_id}/requests` | `can_manage_members` | Pendientes de aprobar (los menores **sólo** aparecen cuando ya hay consentimiento): nombre, edad en años, rol pedido, nota, origen (solicitud / enlace multiuso) |
| `POST /clubs/{club_id}/requests/{membership_id}/approve` `{role?, unit_id?}` | `can_manage_members` + `can_grant_club_role` (Secretaría: sólo `STUDENT`) | `ACTIVE` (traslado si tenía otro club, §5.3). 409 ya decidida |
| `POST /clubs/{club_id}/requests/approve-all` | director | Aprueba todas las `STUDENT` pendientes; devuelve el recuento |
| `POST /clubs/{club_id}/requests/{membership_id}/reject` `{reason?}` | igual que aprobar | `REJECTED` |

### 5.3 Nómina, salida, baja y traslado — y qué pasa con el portafolio (E2)

| Método y ruta | Quién | Qué · errores |
|---|---|---|
| `GET /memberships/me` | yo | Membresía activa (club, iglesia, unidad, consejero, rol, desde cuándo) y pendientes con su estado |
| `DELETE /memberships/me` | yo | Salir: `ENDED (LEFT)`. 409 si soy el único director del club (el relevo lo hace la asociación). Un menor puede salir solo; se avisa a su tutor |
| `GET /clubs/{club_id}/members?status=&unit_id=&role=` | `can_view_roster` | Nómina: `membership_id`, `user_id`, nombre, rol, unidad, `is_minor`, **edad en años (nunca la fecha)**, estado, consentimiento (`status`, nombre del tutor), verificación del personal. `guardian_email` sólo para el director |
| `PATCH /clubs/{club_id}/members/{membership_id}` `{role}` | director o administrador | Cambia el rol dentro del club (`can_grant_club_role`; menores sólo `STUDENT`). Actualiza `users.role` |
| `POST /clubs/{club_id}/members/{membership_id}/remove` `{reason}` | director; Secretaría sólo a `STUDENT` | `ENDED (REMOVED)`, motivo obligatorio; correo al miembro (y tutor). 403 sobre uno mismo o sobre un rol no inferior |
| `PATCH /clubs/{club_id}/profile` `{meeting_day?, meeting_time?, contact?, accepts_requests?}` | `can_manage_members` | «Datos de control» del club en `metadata_json.profile`. Lista blanca; no toca nombre, ubicación ni jerarquía |

**Activar** (`memberships.activate`): pone `users.organization_id = club_id`, `users.role = membership.role`,
`started_at`, y cierra otras pendientes de esa persona (`CANCELLED`). **Terminar** (`memberships.end`): borra
`organization_id`, borra `leader_verified_until`, quita a la persona de `club_units.counselor_id`, y devuelve el rol
a `STUDENT` si era `COUNSELOR` o `CLUB_SECRETARY` (roles que sólo existen dentro de un club); `INSTRUCTOR` y
`STUDENT` se conservan. **Traslado**: aceptar o ser aprobado en el club B estando activo en A termina A
(`TRANSFERRED`) y activa B **en la misma transacción**; exige `confirm_transfer` de la persona y, si es menor,
consentimiento nuevo para B. El fundador recibe su membresía (`FOUNDER`, `CLUB_DIRECTOR`) en
`clubs.stage_pending_club`; su autoridad sigue gobernada por `director_blocked`. `PATCH /users/{id}` con
`organization_id` o `role` sobre una cuenta de club delega en el servicio (`source = 'ADMIN'`).

**Portafolio (A) al cambiar de club.** A calcula la jurisdicción con el club **actual** del miembro, así que el
traslado ya mueve la jurisdicción. Para que la cola de revisión (`honor_enrollments (club_id, status)`) no espere a
la siguiente escritura, `activate` y `end` llaman a `portfolio.on_club_changed(db, user_id, new_club_id | None)`:
`UPDATE honor_enrollments SET club_id = :nuevo WHERE user_id = :u AND status IN ('IN_PROGRESS','READY')`.
- `CERTIFIED` y `WITHDRAWN` no se tocan (A los congela con su club).
- Requisitos `COMPLETE` siguen completos con su revisor original; el nuevo club puede reabrirlos (regla 4 de A).
  Los `SUBMITTED` pasan a la cola del club nuevo; las observaciones `INCOMPLETE` siguen visibles.
- Una inscripción `READY` la certifica el director del club nuevo, y el certificado lleva ese club.
- Sin club nuevo: `club_id = NULL` y aplica el punto 5 del flujo de A («nadie puede dictaminar hasta que…»).
- El club anterior pierde acceso a portafolio y evidencias **en el acto** (los permisos de A se calculan al vuelo).
  Las evidencias no se mueven: su clave lleva `user_id`, no el club.
Si A aún no está desplegado, la llamada no existe; se añade con el primer despliegue conjunto (una línea + test).

### 5.4 Unidades (E5)

| Método y ruta | Quién | Qué · errores |
|---|---|---|
| `GET /clubs/{club_id}/units` | `can_view_roster` | Unidades activas con recuento / cupo, tramo de edad y nombre del consejero |
| `POST /clubs/{club_id}/units` `{name, min_age?, max_age?, capacity?}` | `can_manage_members` | 201; 409 nombre repetido |
| `PATCH /clubs/{club_id}/units/{unit_id}` `{name?, min_age?, max_age?, capacity?}` | `can_manage_members` | 409 si `capacity` < miembros actuales |
| `PUT /clubs/{club_id}/units/{unit_id}/counselor` `{membership_id \| null}` | **director** o administrador | Adulto `ACTIVE` del mismo club con rol `COUNSELOR`, `INSTRUCTOR`, `CLUB_SECRETARY` o `CLUB_DIRECTOR`. 409 «Sin verificar» si no cumple `may_handle_minors` (con el interruptor de E7 apagado, basta ser adulto `ACTIVE`); 400 menor |
| `PUT /clubs/{club_id}/members/{membership_id}/unit` `{unit_id \| null}` | `can_manage_members` | Asigna o mueve. 409 «Unidad llena» (con `SELECT … FOR UPDATE` sobre la unidad); 400 unidad de otro club o membresía no `ACTIVE`. Devuelve `age_warning: true` si la edad queda fuera del tramo |
| `DELETE /clubs/{club_id}/units/{unit_id}` | `can_manage_members` | Archiva. 409 si aún tiene miembros (se mueven primero) |

El **cupo es duro** (el director lo sube en un toque); el **tramo de edad es un aviso** (los cumpleaños lo
desajustan durante el año). El frontend ofrece tramos prefijados de Conquistadores (10–11, 12–13, 14–15, 16+), pero
se guardan números: el CORE es multi-ministerio (Aventureros usará 6–9). Una persona puede ser consejera de más de
una unidad; una unidad tiene un consejero. Una invitación puede traer `unit_id`: al activarse, la membresía entra
ya en la unidad si hay cupo (si no, entra sin unidad y se avisa al director en la respuesta de aprobación).

### 5.5 Zona e iglesia al crear el club (E6)

**Alta.** `ClubSignup` gana `zone_id | zone_name` e `church_id | church_name` (**exactamente uno de cada par**;
`church_id` exige `zone_id` y que la iglesia cuelgue de esa zona; la zona debe colgar de `association_id`). El campo
antiguo `church` se acepta un ciclo como alias de `church_name`. Sin zona o iglesia ⇒ 422.
- Con los dos ids: el club `pending` nace **ya bajo su iglesia** (`…asociación.zona.iglesia.club`).
- Con algún nombre propuesto: nace bajo la asociación, como hoy, con
  `metadata_json.placement = {zone_id, zone_name, church_id, church_name}`.

**Aprobación.** `POST /org-nodes/{id}/approve` acepta un cuerpo opcional `{zone_id | zone_name, church_id |
church_name}`. Si el club no está ubicado, quien aprueba **debe** resolver la ubicación: elegir nodos o crearlos con
la misma lógica de `create_org_node` extraída a `services/placement.py` (jerarquía, alcance, padre activo). Crear
zona: administración de asociación o superior. Crear iglesia: además, el coordinador de esa zona. El director no
crea nodos (D3). Un club **no pasa a `active` sin iglesia y zona**.
Errores: 409 «Falta ubicar el club (zona e iglesia)»; 409 «Ya existe esa iglesia en la zona» con su `id` (comparación
sin acentos ni mayúsculas) para que se elija en vez de duplicar; 403 fuera de la zona del coordinador.

**Clubes que ya existen sin zona ni iglesia (no destructivo).** Siguen funcionando igual; nada se bloquea.
| Método y ruta | Quién | Qué |
|---|---|---|
| `GET /org-nodes/unplaced-clubs` | administradores con alcance | Clubes `active` cuyo padre es una asociación, con la propuesta del director si la hay |
| `PUT /org-nodes/clubs/{club_id}/placement-proposal` `{zone_id \| zone_name, church_id \| church_name}` | director de ese club | Guarda la propuesta en `metadata_json.placement`; no mueve nada |
| `POST /org-nodes/clubs/{club_id}/place` `{…}` | mismas reglas que aprobar | Mueve el club bajo la iglesia |

**Mover** es una transacción: bloquea la fila del club, cambia `parent_id` y reescribe `path` del club y de sus
descendientes (`nuevo_prefijo || subpath(path, nlevel(path_antiguo))`). Los ids no cambian, así que usuarios,
membresías, inscripciones y certificados no se tocan. La auditoría `CLUB_PLACE` guarda padre y `path` anteriores:
deshacerlo es otra llamada a `place`.

**Lecturas que cambian** (hallazgo 4): `nearby_clubs`, `list_pending_clubs` y `_decide_club` obtienen la asociación
como **ancestro** (`type = 'association' AND path @> club.path`) y devuelven `association`, `zone` y `church`
(`OrgRef`); `church` cae a `metadata_json.church` si el club aún no está ubicado. `GET /org-nodes/search` gana
`within=<id>` (sólo nodos bajo ese ancestro) para los selectores de zona e iglesia. La comprobación de nombre
duplicado por asociación ya usa `path <@` y sigue valiendo.
**Carga inicial** (opcional, para NTAM): `migrations/import_zones_churches.py` lee un CSV `zona, iglesia, ciudad`;
simulacro por defecto, `--commit`, idempotente por nombre.

### 5.6 Carta de la iglesia y «Instructor activo verificado» (E7)

Quién la presenta: todo adulto con membresía `ACTIVE` y rol `CLUB_DIRECTOR`, `INSTRUCTOR` o `COUNSELOR` (la
Secretaría no dictamina ni ve evidencias: no se le exige). **Verificado** = carta `APPROVED` vigente **en su club
actual** + `child_protection_completed`. La bandera del curso la sigue poniendo un administrador con alcance
(`POST /users/{id}/child-protection-cert`, ya existe); quien valida la carta puede marcarla en el mismo acto.
Cambiar de club o salir deja la verificación sin efecto: la carta avala a la persona ante **esa** iglesia.

Archivo: `application/pdf`, `image/jpeg`, `image/png`, `image/webp`, máx. 10 MB, en el **bucket privado de A**
mediante `app/services/private_storage.py` (`presign_put` 10 min con `Content-Type` y `Content-Length` fijados,
`head`, `presign_get` 5 min, `delete`). Nunca `media.adventist.club`. Sin `R2_PRIVATE_BUCKET_NAME` ⇒ 503 en estos
endpoints y todo lo demás funciona. El CORS que A pide para el bucket ya cubre esta subida.
`migrations/purge_leader_letters.py` borra del bucket las cartas `REJECTED`, `REVOKED`, vencidas o sin completar
con más de 12 meses (simulacro por defecto, `--commit`); la fila queda como historial.

| Método y ruta | Quién | Qué · errores |
|---|---|---|
| `POST /leader-verifications` `{content_type, size_bytes, sha256?, issued_on, signer_name, signer_position?}` | personal adulto con membresía `ACTIVE` | Crea `PENDING_UPLOAD` y devuelve `{verification, upload: {url, method: "PUT", headers, expires_in}}`. 409 ya hay una en curso, o la vigente vence en más de 60 días; 422 tipo o tamaño; 400 `issued_on` futura o de hace más de 12 meses |
| `POST /leader-verifications/{id}/complete` | quien la subió | `head` comprueba existencia, tamaño y tipo ⇒ `SUBMITTED` y correo a quienes validan; si no, 409 |
| `GET /leader-verifications/me` | yo | Vigente + historial (metadatos, sin URLs) |
| `GET /leader-verifications/queue?status=SUBMITTED` | `can_validate_leader` (por alcance) | Nombre, rol, club, iglesia, zona, fecha, bandera de protección infantil |
| `GET /leader-verifications/{id}/url` | el dueño o `can_validate_leader` | `{url, expires_in: 300}`; cada lectura ajena se audita |
| `POST /leader-verifications/{id}/approve` `{valid_until?, child_protection_completed?}` | `can_validate_leader` | `APPROVED`; `valid_until` por defecto hoy + 12 meses, máximo 24; escribe `users.leader_verified_until`. 409 si no está `SUBMITTED` |
| `POST /leader-verifications/{id}/reject` `{reason}` | `can_validate_leader` | `REJECTED`; puede presentar otra |
| `POST /leader-verifications/{id}/revoke` `{reason}` | `can_validate_leader` | `REVOKED`, borra `leader_verified_until` (la visión: la Asociación puede suspender instructores) |

Nadie valida su propia carta. El **director no ve las cartas de su personal**, sólo el estado. Vencimiento: aviso en
el panel desde 30 días antes; el correo lo envía `migrations/notify_expiring_letters.py` (idempotente gracias a
`notification_log`), pensado para un Cron Job de Render cuando el responsable lo active.

### 5.7 Rol Secretaría de club — `CLUB_SECRETARY` (E8)

Adulto, nombrado por el director con invitación nominal o con `PATCH …/members/{id}`. Alcance: **su club**.
| Puede | No puede |
|---|---|
| Ver la nómina con el serializador recortado (§5.3) | Dictaminar, certificar, ver portafolios o evidencias (`can_review`, `can_issue`, `can_view_portfolio` no lo incluyen) |
| Crear y revocar invitaciones de `STUDENT` | Conceder o quitar roles de personal; invitar personal |
| Aprobar o rechazar solicitudes de `STUDENT` | Asignar consejeros |
| Crear, editar y archivar unidades; asignar miembros | Ver `guardian_email`, fechas de nacimiento, cartas |
| Dar de baja a un `STUDENT` con motivo | Dar de baja a personal, al director o a sí misma |
| Editar los datos de control del club (`PATCH …/profile`) | Cambiar nombre, ubicación, zona o iglesia; usar `GET /users` |

Hasta E8 las invitaciones no admiten este rol (constante `INVITABLE_ROLES`). La Secretaría de Asociación de la
visión (reasignar cargos entre clubes, puntajes) queda para su bloque (D1).

### 5.8 2FA obligatorio para `MASTER_GC` (E1)

- Política: `MFA_REQUIRED_ROLES = (MASTER_GC,)` en `security.py` (ampliarla a otros roles es una línea).
- **Punto de control: `app/deps.py`.** El actual `get_current_user` se divide en `get_authenticated_user` (lo de
  hoy) y `get_current_user` = autenticado + política. Ningún router cambia de import. Los tokens que nacen en
  `POST /auth/mfa/verify` llevan el *claim* `mfa: true`; `refresh` lo copia al nuevo access token.
  Para un rol obligado: sin `mfa_enabled` ⇒ **403 «Debes activar la verificación en dos pasos»** en todo salvo
  `GET /auth/me`, `GET /users/me`, `POST /auth/mfa/setup` y `POST /auth/mfa/verify-setup` (que dependen de
  `get_authenticated_user`); con `mfa_enabled` pero token sin el *claim* ⇒ **401 «Vuelve a iniciar sesión con tu
  código»**.
- **Alta sin periodo de gracia**: la cuenta obligada que aún no lo tiene sólo puede completar el alta. `login`
  añade `mfa_enrollment_required` a su respuesta; `verify-setup` devuelve los códigos de recuperación y
  `reauth_required: true`. Como ascender a `MASTER_GC` ya exige MFA, esto sólo afecta a cuentas sembradas: el
  despliegue empieza listándolas (`SELECT email FROM users WHERE role = 'MASTER_GC' AND NOT mfa_enabled`) y dándolas
  de alta **antes** de activar el control, para que nadie pueda inscribir un segundo factor con sólo la contraseña.
- **Recuperación**: 10 códigos de un solo uso (`XXXXX-XXXXX`, guardados como hash) mostrados una vez;
  `POST /auth/mfa/verify` acepta `recovery_code` en lugar de `totp_code` (mismo límite de intentos), lo marca usado
  y avisa por correo. `POST /auth/mfa/recovery-codes` `{totp_code}` los regenera. Sin códigos:
  `POST /users/{id}/mfa-reset` `{reason}` — sólo otro `MASTER_GC`, nunca sobre sí mismo (el «consejo colegiado» de
  la visión), correo al afectado. Último recurso, con un solo MASTER: `migrations/reset_mfa.py --email … --commit`,
  que exige acceso a la base y deja fila en `audit_log`.
- Fuera de alcance: doble aprobación de acciones críticas, WebAuthn, revocación de tokens emitidos.

### 5.9 Menores y tutores (E3, con retoques en E2)

Estado inicial de la membresía según origen y edad:
| Origen | Adulto | Menor |
|---|---|---|
| Invitación de un solo uso | `ACTIVE` | `PENDING_CONSENT` → `ACTIVE` |
| Enlace multiuso o solicitud | `PENDING_APPROVAL` → `ACTIVE` | `PENDING_CONSENT` → `PENDING_APPROVAL` → `ACTIVE` |

«Menor» se decide con `birth_date` si existe (misma función `_is_minor` de `auth.py`, que se mueve a un módulo
compartido) y, si no, con `users.is_minor`.

1. El menor acepta la invitación o solicita ingreso e indica `guardian_email` (si ya tiene un tutor `APPROVED`, no
   hace falta: se avisa a sus tutores). Se genera un token de consentimiento (14 días, hash) y se envía el correo.
2. El tutor abre `/consent?t=…`, inicia sesión o crea cuenta (`PARENT_GUARDIAN`), y ve: nombre del menor, club,
   iglesia, ciudad, nombre del director, rol, y **qué verá el club** (nombre, edad, avance, evidencias) y quién
   (director e instructores verificados). Autoriza o no.
3. Autorizar crea o actualiza la tutela (`guardianships`, `APPROVED`, `consent_granted_at`) y avanza la membresía.
   No autorizar ⇒ `CANCELLED (DECLINED)`.

| Método y ruta | Quién | Qué · errores |
|---|---|---|
| `POST /memberships/consents/preview` `{token}` | adulto autenticado | Resumen del punto 2. Un único 404 para cualquier token no válido |
| `POST /memberships/consents/decide` `{token \| membership_id, decision: APPROVE \| REJECT, relationship?}` | adulto con correo verificado, que tiene el token **o** una tutela `APPROVED` sobre ese menor | 403 si es el propio menor o un menor; 409 ya decidida |
| `POST /memberships/{id}/consent/resend` `{guardian_email?}` | el menor | Reenvía o corrige el correo mientras esté `PENDING_CONSENT`; 3 al día |
| `POST /memberships/{id}/consent/revoke` | tutor con tutela `APPROVED` | `ACTIVE` → `ENDED (CONSENT_REVOKED)`; aplica §5.3 al portafolio |
| `GET /users/guardianships/my-children` (ampliado) | adulto | Añade nombre del menor, club, unidad, estado de la membresía y consentimientos pendientes |

Cambios en lo existente (D9): `create_guardianship` y `my_children` cambian «rol `PARENT_GUARDIAN`» por «adulto».
La cuenta que decide no tiene que usar el mismo correo al que se envió (los padres se lo reenvían); se guardan
ambos. La plataforma no puede comprobar el parentesco: el control humano es el director, que ve en la nómina quién
autorizó a cada menor. **Lo que ve el tutor:** sus menores, su club y unidad, consentimientos pendientes,
«Retirar autorización» y el portafolio en lectura (A). No ve a otros miembros ni la nómina.

### 5.10 Notificaciones por correo (E3–E9) — transaccionales, sin marketing

`services/email.py` (Resend, `base_template`) + `services/notifications.py`, que decide destinatarios, consulta
`notification_log` y encola. Sin `RESEND_API_KEY` no se envía nada (como hoy) y los enlaces se siguen pudiendo
compartir a mano. Español hasta que exista `users.locale`. Sin píxeles, sin adjuntos, sin imágenes de evidencias.

| Correo | A quién | Cuándo | Incr. |
|---|---|---|---|
| Invitación al club | `email` de la invitación | al crearla con correo | E3 |
| Solicitud de consentimiento | `guardian_email` o tutores `APPROVED` | menor acepta o solicita; reenvío manual | E3 |
| «Tienes solicitudes por revisar» | director(es) del club | sólo cuando la cola pasa de 0 a 1; **sin nombres de menores**, sólo el recuento y el enlace | E4 |
| Decisión de membresía (aprobada, rechazada, baja con motivo) | la persona; si es menor, también sus tutores | al decidir | E4 |
| Carta recibida | quienes cumplen `can_validate_leader` (coordinadores de la zona; si no hay, administración de la asociación) | al completar la subida | E7 |
| Carta validada / rechazada / revocada / por vencer | el líder | al decidir; 30 días antes (script) | E7 |
| Seguridad: MFA restablecido, código de recuperación usado | el titular | siempre | E1 |
| Avance del portafolio (lo que A dejó para E): requisito **incompleto** con observación, inscripción **lista para certificar**, **certificado emitido** | el miembro; los tutores sólo en «certificado emitido» | tras el commit del dictamen o la emisión. Un `COMPLETE` suelto no envía correo. Como mucho uno por inscripción cada 12 h (`notification_log`). Respeta `users.notify_progress` | E9 |

E9 toca el router de A sólo para añadir la tarea en segundo plano tras el commit; la lógica de A no cambia.

## 6. Reglas de integridad (en los servicios, con tests)

1. `users.organization_id` de una cuenta de club = `club_id` de su única membresía `ACTIVE`, o NULL. Sólo
   `memberships.activate / end` lo escriben.
2. Un menor nunca pasa a `ACTIVE` sin `consent_at` para esa membresía; un menor sólo recibe el rol `STUDENT`.
3. Nadie concede un rol que no supera (`outranks`); la Secretaría sólo gestiona `STUDENT`; nadie se aprueba, se da
   de baja por el camino de gestión, ni valida su propia carta.
4. Invitaciones: consumo atómico; multiuso sólo `STUDENT` y siempre con aprobación; personal siempre nominal.
5. Unidad y miembro pertenecen al mismo club; cupo duro; consejero adulto, `ACTIVE` y con `may_handle_minors`.
6. Un club no pasa a `active` sin ancestros `church` y `zone`; mover un club conserva ids y audita el `path` anterior.
7. La verificación de liderazgo vale sólo en el club para el que se validó y hasta `valid_until`; terminar la
   membresía la deja sin efecto.
8. Traslado = terminar A + activar B en una transacción; nunca dos `ACTIVE` (lo garantiza además el índice único).
9. Un rol en `MFA_REQUIRED_ROLES` no opera sin `mfa_enabled` y sin un token nacido de un segundo factor.

## 7. Privacidad de menores

- La nómina muestra **edad en años**, nunca la fecha de nacimiento ni el correo del menor; `guardian_email` sólo al
  director. La Secretaría y los consejeros no pasan por `GET /users/{id}`.
- Un menor que **solicita** ingreso no es visible para el club hasta que su tutor autoriza. Los invitados por el
  propio club sí aparecen como «esperando autorización» (el club ya los conoce).
- Personal sin verificar no ve datos, portafolio ni evidencias de menores (§4).
- `preview` de invitación y de consentimiento: un solo 404, sin nombres de personas, con límite de intentos.
- Tokens sólo como hash, sólo en cuerpos JSON. El frontend guarda el token en `sessionStorage` y limpia la URL
  (`history.replaceState`) antes de cualquier navegación, para que no viaje en `next=`, *Referer* ni registros.
- Los correos a terceros (director, validadores) no llevan nombres de menores. La auditoría guarda ids, nunca
  tokens ni correos de tutores en `details`.
- Las cartas viven en el bucket privado, con URL firmada de 5 minutos que no se guarda ni se cachea, lectura
  auditada y purga a los 12 meses.
- Al salir, ser dado de baja o retirarse el consentimiento, el club pierde el acceso en el acto.

## 8. Auditoría (`audit_log`, `record_audit` en la misma transacción)

`MEMBERSHIP_REQUEST`, `MEMBERSHIP_INVITE_ACCEPT`, `MEMBERSHIP_APPROVE`, `MEMBERSHIP_REJECT`, `MEMBERSHIP_CANCEL`,
`MEMBERSHIP_LEAVE`, `MEMBERSHIP_REMOVE`, `MEMBERSHIP_TRANSFER`, `MEMBERSHIP_ROLE_CHANGE`, `CONSENT_REQUEST`,
`CONSENT_GRANT`, `CONSENT_REJECT`, `CONSENT_REVOKE`, `INVITATION_CREATE`, `INVITATION_REVOKE`, `UNIT_CREATE`,
`UNIT_UPDATE`, `UNIT_ARCHIVE`, `UNIT_ASSIGN`, `UNIT_COUNSELOR`, `CLUB_PROFILE_UPDATE`, `CLUB_PLACEMENT_PROPOSE`,
`CLUB_PLACE`, `LEADER_LETTER_SUBMIT`, `LEADER_LETTER_VIEW`, `LEADER_VERIFY_APPROVE`, `LEADER_VERIFY_REJECT`,
`LEADER_VERIFY_REVOKE`, `MFA_RECOVERY_REGENERATE`, `MFA_RECOVERY_USED`, `MFA_RESET`. Se mantienen `CLUB_REQUEST`,
`CLUB_APPROVE`, `CLUB_REJECT` (ahora con zona e iglesia en `metadata`). `entity_type`: `MEMBERSHIP`, `INVITATION`,
`UNIT`, `ORGANIZATION`, `LEADER_VERIFICATION`, `USER`. Todas caben en `varchar(40)`.

## 9. Frontend (`conquistadores.app`) — kit `cq-*`, móvil primero, escala única, skeletons reales

- Proxy `app/api/v1/[...path]`: `ALLOWED_PREFIXES += clubs, memberships, leader-verifications`; `PUBLIC_PATHS +=
  memberships/invitations/preview`. `auth/mfa/verify` sigue vetado: el puente `app/api/session/mfa` pasa
  `recovery_code`, y `app/api/session/register` pasa `invitation_token` y `guardian_email`.
- `lib/api/memberships.ts`, `lib/api/leader-verifications.ts` + tipos; `lib/api/roles.ts`: etiquetas nuevas,
  `isClubManager`, `isClubStaff`; `isGuardian` pasa a «tiene menores a cargo o rol de tutor».
- **`/join`**: captura el token, vista previa (club, iglesia, rol, «el director confirmará tu ingreso» si aplica).
  Sin sesión: **Crear cuenta** (el registro oculta el selector de rol —lo fija la invitación— y pide el correo del
  tutor si es menor) o **Ya tengo cuenta**. Con sesión: **Unirme**; si ya tiene club, *action sheet* de traslado.
- **`/consent`**: resumen, «qué verá el club», parentesco, **Autorizar / No autorizar**.
- **`/clubs`**: cada club cercano con **Solicitar unirme** (hoja: nota y, si es menor, correo del tutor) o «No
  recibe solicitudes»; píldora «Solicitud enviada».
- **Panel — todos**: tarjeta **Mi club** (club, iglesia, unidad, consejero, estado; estados pendientes con
  explicación; «Salir del club» en *action sheet*). Sin club: enlace a `/clubs` y «Tengo un enlace de invitación».
- **Panel — director y Secretaría**: nueva ruta **`/panel/club`** (la navegación de 5 elementos no cambia) con
  control segmentado **Miembros · Solicitudes · Invitaciones · Unidades**. Miembros: filtros por unidad y rol,
  acciones por fila (unidad, rol —sólo director—, baja con motivo). Solicitudes: aprobar, rechazar, «Aprobar
  todas». Invitaciones: hoja de creación (rol, un uso / multiuso, vencimiento, correo) → tarjeta con enlace,
  **Copiar**, **WhatsApp**, **Compartir** (`navigator.share`) y QR. Unidades: rejilla `cq-grid--fit` de tarjetas
  (nombre, tramo, n / cupo, consejero), hoja de edición, detalle con miembros y «Añadir miembros».
- **Alta y «Mi club» del director**: `ClubFields` sustituye el texto libre de iglesia por **zona** e **iglesia**
  con `OrgPicker` (generaliza `association-picker` con `type` y `within`) y la salida «No está en la lista» →
  nombre propuesto. Aviso «Completa zona e iglesia» en clubes sin ubicar. `panel/page.tsx` deja de usar
  `org.parent_id` como asociación.
- **Panel — coordinación y administración**: «Clubes por aprobar» muestra la propuesta y la hoja de aprobación
  resuelve la ubicación; **Clubes por ubicar**; **Cartas por validar** (abre la carta con URL firmada, aprobar con
  vigencia y casilla de protección infantil, rechazar con motivo).
- **Panel — personal**: tarjeta **Verificación de liderazgo** (estado, vence el…, subir o renovar carta: archivo,
  fecha, quién firma; barra de progreso; las fotos se recodifican en el navegador como en A).
- **Panel — tutor**: **Mis menores** con nombre, club, unidad, pendientes y enlace al portafolio.
- **2FA**: `/auth/mfa-enroll` obligatorio para `MASTER_GC` (QR, código, códigos de recuperación con «Ya los
  guardé»), «Usar un código de recuperación» en el paso TOTP del login, y regenerar códigos en Perfil.
- Componentes nuevos del kit, con entrada en `/styleguide` y su skeleton: `OrgPicker`, `InviteSheet`,
  `InviteLinkCard`, `MemberRow`, `RequestRow`, `UnitCard`, `UnitSheet`, `LeaderVerificationCard`,
  `LetterReviewSheet`, `ConsentSummary`, `RecoveryCodes`.

## 10. Pruebas

Backend (pytest de integración, TDD, mismo arnés que `tests/test_club_signup.py`):
- **2FA**: MASTER sin MFA ⇒ 403 en todo salvo el alta; token sin *claim* ⇒ 401; `refresh` conserva el *claim*;
  código de recuperación de un solo uso; `mfa-reset` sólo por otro MASTER; los demás roles no cambian.
- **Membresía**: relleno idempotente; `register` con `organization_id` ⇒ 400; invariante
  `organization_id` ↔ `ACTIVE` tras activar, salir, baja, traslado y `PATCH /users`; único `ACTIVE`; director único
  no puede salir; `GET /users` cerrado a miembros; `on_club_changed` mueve `IN_PROGRESS` / `READY` y respeta
  `CERTIFIED`; el revisor del club anterior pierde `can_review` y la URL de evidencia.
- **Invitaciones**: un uso vs multiuso; consumo concurrente (dos aceptaciones, un cupo); vencida, revocada y
  agotada dan el mismo 404; nominal con otro correo ⇒ 403; la Secretaría no invita personal; registro + aceptación
  atómico (token malo no deja cuenta).
- **Consentimiento**: tabla de estados de §5.9 completa; token de un uso y caducidad; decide un adulto verificado,
  nunca el menor; tutela creada `APPROVED`; revocar termina la membresía; el menor solicitante no aparece en
  `GET …/requests` hasta el consentimiento; adulto no `PARENT_GUARDIAN` puede ser tutor.
- **Unidades**: cupo bajo concurrencia; unidad de otro club; aviso de edad; consejero sin verificar ⇒ 409; el
  consejero ve sólo su unidad.
- **Zona e iglesia**: alta sin zona ⇒ 422; con ids nace bajo la iglesia; con nombres, aprobar exige ubicar; el
  coordinador de zona no crea zonas ni ubica fuera de la suya; iglesia duplicada ⇒ 409 con id; mover conserva ids
  y reescribe `path` de descendientes; `nearby` devuelve la asociación correcta antes y después de mover; se
  actualizan los tests de `test_club_signup.py` que dependían del alcance por asociación.
- **Carta**: firma con cliente simulado; `complete` rechaza tamaño o tipo distinto; matriz de `can_validate_leader`
  (su zona, otra zona, asociación, MASTER, uno mismo); aprobar escribe `leader_verified_until`; vencida, revocada o
  cambio de club ⇒ sin verificar; perder la verificación no quita al consejero de su unidad pero le cierra la vista
  de menores; gracia del director (día 59 sí, día 61 no); **instructor sin verificar no
  dictamina ni ve evidencias de un menor y sí dictamina a un adulto**; interruptor apagado ⇒ comportamiento actual.
- **Secretaría**: matriz completa de §5.7, incluido que queda fuera de `can_review`, `can_issue`,
  `can_view_portfolio` y `GET /users`.
- **Notificaciones**: destinatarios correctos, sin nombres de menores a terceros, regla 0→1, tope de 12 h,
  `notify_progress = false`, y que un fallo de Resend no rompe la petición.
- Auditoría de cada acción; los ~131 tests actuales siguen en verde.
Frontend: `tsc`, `eslint`, `next build`; `scripts/e2e-auth.mjs` ampliado (invitar → registrarse con el enlace →
consentimiento → aprobar → unidad → traslado → salir; carta: subir → validar; MFA obligatorio con un MASTER de
prueba) y verificación en navegador a 375, 768, 1280 y 1920 px, claro y oscuro.

## 11. Incrementos (en orden de dependencia; cada uno se despliega solo)

| # | Incremento | Migración | Depende de | Entrega |
|---|---|---|---|---|
| E1 | 2FA obligatorio `MASTER_GC` | `008` | — | Política en `deps.py`, *claim* `mfa`, códigos de recuperación, `mfa-reset`, script, pantallas de alta y recuperación. Va primero: es pequeño y protege todo lo que sigue |
| E2 | Núcleo de membresía | `008b` | — | Roles nuevos, `club_memberships` + relleno, servicio único, cierre de `register.organization_id` y de `GET /users`, `GET/DELETE /memberships/me`, nómina, baja, cambio de rol, `on_club_changed`, tutor = cualquier adulto. Pantallas: Mi club, `/panel/club` → Miembros |
| E3 | Invitaciones + consentimiento del tutor | `008c` | E2 | Invitaciones (un uso, multiuso, nominales), `/join`, registro con invitación, flujo de consentimiento y `/consent`, «Mis menores» ampliado, correos de invitación y consentimiento |
| E4 | Solicitudes desde `/clubs` y traslado | — | E3 | Solicitar, aprobar / rechazar / aprobar todas, cerrar solicitudes, traslado con confirmación, correos de cola y de decisión |
| E5 | Unidades | `008d` | E2 (E7 para exigir consejero verificado) | `club_units`, asignación, consejero, vista del consejero. Hasta E7 el consejero sólo necesita ser adulto `ACTIVE` |
| E6 | Zona e iglesia | `008e` | — (independiente; puede adelantarse) | `ClubSignup` con zona e iglesia, aprobar ubicando, clubes por ubicar y mover, alcance real del coordinador de zona, lecturas por ancestro, `OrgPicker`, importador CSV |
| E7 | Carta de la iglesia | `008f` | E2, bucket privado de A; mejor tras E6 | Subida, cola de validación, estados y vigencia, `may_handle_minors` en los permisos de A, distintivo, purga y aviso de vencimiento. Se activa con `LEADER_VERIFICATION_ENFORCED_FROM` |
| E8 | Secretaría de club | — | E3, E5 | `CLUB_SECRETARY` invitable, `PATCH …/profile`, pantallas recortadas, matriz de permisos |
| E9 | Correos de avance del portafolio | `008g` | A desplegado, E3 (`notification_log`) | Incompleto / lista / certificada, tope de 12 h, preferencia `notify_progress` |

**Despliegue de cada incremento:** aplicar su migración en Neon **antes** del backend → backend → frontend. E1:
antes, dar de alta el MFA de todo `MASTER_GC` existente. E2: tras el relleno, revisar a mano las membresías
`BACKFILL`. E6: comprobar duplicados antes del índice único; cargar zonas e iglesias de NTAM (importador o
`POST /org-nodes`) y ubicar los clubes existentes. E7: requiere `R2_PRIVATE_BUCKET_NAME` (lo crea el responsable
para A); fijar `LEADER_VERIFICATION_ENFORCED_FROM` cuando haya al menos un validador por asociación activa. Nada
destructivo; cada paso se deshace desactivando código, no datos.

## 12. Compatibilidad comprobada

- **Con A:** la jurisdicción sigue saliendo de `users.organization_id`; E sólo añade una condición para menores
  dentro de `can_review` / `can_issue` / `can_view_portfolio` y un punto más de refresco de
  `honor_enrollments.club_id`. «Sin club nadie dictamina» (A, flujo 5) es justo el estado de quien no tiene membresía
  `ACTIVE`. E reutiliza el bucket privado y `private_storage.py` con otro prefijo y entrega los correos que A
  aplazó. Diferencia anotada: `APPROVED`, no `GRANTED` (hallazgo 6).
- **Con el código:** `director_blocked`, `can_decide_club`, `outranks`, `org_in_user_scope`, `record_audit`,
  `verification._claim` (patrón), `generate_url_token`, `base_template` y el patrón «commit y luego correo» se
  reutilizan. No cambia el contrato de `approve` / `reject` de clubes (el cuerpo nuevo es opcional), ni
  `SELF_REGISTRATION_ROLES`, ni la regla de `PATCH /users` que exige protección infantil para **ascender** a
  `INSTRUCTOR` (por invitación se puede ser instructor sin ella, igual que hoy por registro; lo que limita es §4).
  Cambios de contrato deliberados: `register` rechaza `organization_id`; `GET /users` se cierra; `ClubSignup` exige
  zona e iglesia; las respuestas de clubes añaden `zone` y `church`.

## Fuera de alcance de E

Puntajes de clubes y Secretaría de Asociación; reportes ANT y Club de Honor; eventos, check-in y pagos; relevo de
director asistido (hoy: `PATCH /users/{id}` por un administrador); varios clubes a la vez; cuentas de menor sin
correo gestionadas por el tutor (D8); recalcular `users.is_minor` al cumplir 18; cupo y lista de espera de club;
mensajería o chat; notificaciones push o WhatsApp API; curso de protección infantil dentro de la plataforma;
verificación del parentesco; doble aprobación de acciones críticas de `MASTER_GC`, WebAuthn y revocación de
tokens; ficha pública de club y filtros de `/clubs`; fusión de iglesias o zonas duplicadas; `users.locale` y correos
en otros idiomas.
