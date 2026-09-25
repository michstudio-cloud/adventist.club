# Lanzamiento · Eventos (025) + roles con ámbito (026)

Runbook de producción para el backend de eventos y roles (spec
`docs/superpowers/specs/2026-09-24-eventos.md`). Rama preparada: `release/eventos` = `feat/eventos-integracion`
+ `origin/main` (selector de ministerio 024, traducciones). **Nada de esto se ha ejecutado en producción.**

Leyenda: 🙋 lo hace el propietario (consolas de Neon / Render / Vercel) · 🔧 terminal con acceso a la base.

Orden obligatorio: **snapshot → migraciones → backend → frontend → seed → humo → (confirmación) invitaciones.**
Las migraciones van antes del código: el código nuevo mapea `events`, `role_assignments` y `org_invitations`
y falla sin ellas; el código actual ignora tablas nuevas, así que migrar primero no rompe nada.

---

## 0. Antes de empezar

- `PROD_DB` = cadena de conexión **directa** (no pooler) de la rama `production` del proyecto Neon
  `adventist.club`, formato libpq (`postgresql://…?sslmode=require`). Se usa sólo en esta terminal.
- `psql` ≥ 16 y un venv con `backend/requirements.txt` + `pip install "psycopg[binary]"` (lo usa el seed).
- Checkout de `release/eventos` (o de `main` ya fusionado) en `backend/`.
- Ventana tranquila: el backfill de 026 copia `users.role` en el momento de aplicarse (ver §3.4).

## 1. Snapshot manual en Neon 🙋

Neon → proyecto `adventist.club` → rama `production` → **Backup & Restore → Create snapshot**, nombre:

```
pre-eventos-roles-025-026-AAAA-MM-DD
```

(con la fecha del día). Anotar la hora exacta (UTC) en que se creó: es el punto de vuelta atrás completo.

## 2. Qué migraciones faltan en producción 🔧

No hay tabla de control de migraciones: se comprueba por el esquema. Todas son idempotentes
(`IF NOT EXISTS`), así que aplicar de nuevo una ya aplicada no cambia nada, pero se aplican **sólo las `f`**.

```sql
-- psql "$PROD_DB" -At  (sólo lectura)
SELECT m, applied FROM (VALUES
 ('020_signatures',        EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name='signature_url')),
 ('021_certificate_signatures', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='certificates' AND column_name='signature_director_url')),
 ('022_club_ministries',   to_regclass('public.organization_ministries') IS NOT NULL),
 ('023_certificate_text_overrides', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='certificates' AND column_name='text_overrides')),
 ('024_master_guide_catalog', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name='active_ministry_id')),
 ('025_events',            to_regclass('public.events') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='event_registrations' AND column_name='tiebreak_rank')),
 ('026_role_assignments',  to_regclass('public.role_assignments') IS NOT NULL AND to_regclass('public.org_invitations') IS NOT NULL),
 ('027_event_branding',    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='events' AND column_name='brand_accent'))
) AS t(m, applied) ORDER BY m;
```

Esperado hoy: 020–024 `t` (024 viene de `main`, que ya está desplegado: si saliera `f`, el
`/auth/me` actual ya estaría fallando — aplicar 024 primero) y 025–026 `f`.

Numeración: 024 = catálogo de Guías Mayores (main), 025 = eventos, 026 = roles e invitaciones.
Sin choques. 027 = marca por evento (`brand_logo_url`, `brand_color`, `brand_accent` en `events`;
aditiva, idempotente; aplicar antes del código de historial/marca). Siguiente libre **028**.

### 2.1 Foto previa de roles (sólo lectura, para comparar después)

```sql
SELECT role, count(*) AS n, count(*) FILTER (WHERE organization_id IS NULL) AS sin_org
FROM users GROUP BY role ORDER BY n DESC;

-- Informativo: cuentas cuyo rol de club no tiene membresía ACTIVE. El backfill las copia tal
-- cual (no inventa ni corrige nada); anotar la lista.
SELECT u.email, u.role FROM users u
WHERE u.role IN ('CLUB_DIRECTOR','CLUB_SECRETARY','INSTRUCTOR','COUNSELOR')
  AND NOT EXISTS (SELECT 1 FROM club_memberships m WHERE m.user_id = u.id AND m.status = 'ACTIVE');

-- Informativo: membresía ACTIVE que no coincide con users.role / organization_id.
SELECT u.email, u.role, m.role AS membresia FROM users u
JOIN club_memberships m ON m.user_id = u.id AND m.status = 'ACTIVE'
WHERE (u.role, u.organization_id) IS DISTINCT FROM (m.role, m.club_id);
```

## 3. Migraciones 🔧 (en este orden, cada una en una transacción)

```bash
cd backend/migrations
psql "$PROD_DB" -1 -v ON_ERROR_STOP=1 -f 025_events.sql
psql "$PROD_DB" -1 -v ON_ERROR_STOP=1 -f 026_role_assignments.sql
```

- 025: crea 8 tablas (`events`, `event_activities`, `event_adjustment_types`, `event_staff`,
  `event_registrations`, `evaluations`, `evaluation_revisions`, `event_adjustments`), índices, la
  función + trigger que hace inmutable `evaluation_revisions`. Los `NOTICE … already exists, skipping`
  son normales (columnas que el mismo archivo añade con `ADD COLUMN IF NOT EXISTS`).
- 026: crea `org_invitations` y `role_assignments` y hace el **backfill**: una asignación ACTIVE y
  principal (`source = 'BACKFILL'`) por usuario con su `users.role` + `users.organization_id`. Debe decir
  `INSERT 0 <número de usuarios>`.

### 3.1 Verificación del backfill (ambas deben dar 0)

```sql
-- usuarios sin exactamente una asignación ACTIVE principal
SELECT count(*) FROM (
  SELECT u.id FROM users u
  LEFT JOIN role_assignments ra ON ra.user_id = u.id AND ra.status = 'ACTIVE' AND ra.is_primary
  GROUP BY u.id HAVING count(ra.id) <> 1) x;
-- principal que no coincide con users.role / organization_id
SELECT count(*) FROM users u WHERE NOT EXISTS (
  SELECT 1 FROM role_assignments ra WHERE ra.user_id = u.id AND ra.status = 'ACTIVE'
    AND ra.is_primary AND ra.role = u.role AND ra.organization_id IS NOT DISTINCT FROM u.organization_id);
```

### 3.2 Si algo falla

`-1` + `ON_ERROR_STOP` deshace el archivo entero: la base queda como antes de ese archivo. Parar, copiar
el error, no desplegar el backend.

### 3.3 Re-ejecución

Aplicar de nuevo 025/026 es un no-op (026 dice `INSERT 0 0`). Comprobado en el ensayo (§10).

### 3.4 Usuarios registrados entre la migración y el despliegue

El código actual sigue creando cuentas sin fila en `role_assignments`. No rompe nada (`effective_roles`
lee `users.role`, y `sync_legacy` crea la fila en la siguiente escritura), pero para dejarlo limpio:
**repetir 026 después del paso 4** (sólo inserta a quien no tiene ninguna asignación).

## 4. Backend 🙋/🔧

1. Fusionar `release/eventos` en `main` (sin squash: conserva el merge con `origin/main`) y `git push origin main`.
2. Render (`adventist-club-api`, autoDeploy desde `main`) despliega solo. Esperar a **Live** y a que
   `GET https://api.adventist.club/api/v1/health` responda `{"ok":true,…,"database":"connected"}`.
3. Repetir 026 (§3.4) y la verificación §3.1.

Variables de Render que este lanzamiento usa (no hay variables nuevas):

| Variable | Valor esperado | Para qué |
|---|---|---|
| `CORS_ORIGINS` | ya incluye `https://events.adventist.club` (además de conquistadores.app, adventist.club, admin) | llamadas del subdominio de eventos |
| `FRONTEND_URL` | la web que sirve `/invitacion` (el proyecto Next común; hoy `https://adventist.club` o `https://conquistadores.app`) | enlace de invitación: `<FRONTEND_URL>/invitacion?t=<token>` (`org_invitations.join_url`); sin `FRONTEND_URL` usa `PUBLIC_WEB_URL` |
| `RESEND_API_KEY` | ya configurada | correo de invitación |

Ojo: `render.yaml` del repo aún lista `CORS_ORIGINS=https://conquistadores.app,https://www.conquistadores.app`.
Si el servicio se sincroniza por Blueprint, ese valor pisaría el de la consola: actualizar `render.yaml`
o no sincronizar el Blueprint. Comprobar CORS con:

```bash
curl -si -X OPTIONS https://api.adventist.club/api/v1/events \
  -H 'Origin: https://events.adventist.club' -H 'Access-Control-Request-Method: GET' | grep -i access-control-allow-origin
# → access-control-allow-origin: https://events.adventist.club
```

## 5. Frontend 🙋

Proyecto Next común (el que sirve conquistadores.app / adventist.club / admin): rama `feat/eventos-web`
→ `main` → Vercel. Incluye `/eventos/*`, la pantalla «Equipo» y `/invitacion` para invitaciones de
organización. Dominio `events.adventist.club` añadido en Vercel (🙋, spec §4). Desplegar **después**
del backend: el front nuevo llama a `/api/v1/events` y `/api/v1/org-invitations/*`.

## 6. Seed de la plantilla «Universo de Dios» 🔧

Requiere la asociación con `code = 'NTAM'`. Comprobar:

```sql
SELECT id, parent_id, type, code, path FROM organizations WHERE code = 'NTAM';
```

Si no existe, crearla bajo su unión (sustituir el código real de la Unión Mexicana del Norte;
`path` = ruta del padre + id sin guiones, igual que hace la app):

```sql
BEGIN;
INSERT INTO organizations (id, parent_id, type, name, code, status, path)
SELECT n.id, u.id, 'association', 'Asociación Norte de Tamaulipas', 'NTAM', 'active',
       (u.path::text || '.' || replace(n.id::text, '-', ''))::ltree
FROM organizations u, (SELECT gen_random_uuid() AS id) n
WHERE u.code = '<CÓDIGO_UNIÓN>' AND u.type = 'union'
  AND NOT EXISTS (SELECT 1 FROM organizations WHERE code = 'NTAM');
SELECT id, parent_id, code, path FROM organizations WHERE code = 'NTAM';  -- 1 fila, path no NULL
COMMIT;
```

Luego:

```bash
cd backend/migrations
DATABASE_URL="$PROD_DB" python seed_event_template_universo.py --association NTAM --operator <tu-usuario>            # simulacro (ROLLBACK)
DATABASE_URL="$PROD_DB" python seed_event_template_universo.py --association NTAM --operator <tu-usuario> --commit   # escribe
```

Esperado: `"created": true`, `DRAFT`, 11 actividades, 10 tipos de ajuste, `to_define` = Inspección
sábado, Inspección domingo, Evento previo. Una segunda ejecución responde `"created": false` y no toca
nada (el evento pudo haberse editado). Deja una fila `EVENT_SEED` en `audit_log`.

## 7. Humo 🔧

```bash
API=https://api.adventist.club/api/v1
curl -s $API/health                                     # ok + database connected
# Token de una cuenta MASTER: POST $API/auth/login {email,password}; si responde mfa_required,
# POST $API/auth/mfa/verify {temp_token, totp_code}. Usar el access_token:
curl -s $API/auth/me -H "Authorization: Bearer $TOKEN"  # role=MASTER_GC, roles=[{role:MASTER_GC, principal:true, organization:null}], ministries_available…
curl -s $API/events  -H "Authorization: Bearer $TOKEN"  # 200, lista con «Camporee Familiar de Aventureros 2026 — Universo de Dios» (DRAFT)
```

Además, con una cuenta de club (director): `/auth/me` trae `roles` con su club como principal y
`/events` responde 200 (lista vacía mientras no haya inscripción). En la web: abrir
`https://events.adventist.club`, iniciar sesión como MASTER y ver el evento en borrador.

## 8. Vuelta atrás

Del menos al más invasivo:

1. **Frontend**: Vercel → Deployments → el anterior → *Instant Rollback*.
2. **Backend**: Render → `adventist-club-api` → Deploys → el anterior → *Rollback* (o `git revert` del
   merge en `main` y push). El código anterior ignora las tablas nuevas: **no hace falta tocar la base**.
3. **Base, quitando sólo lo nuevo** (con el backend ya en la versión anterior; pierde eventos,
   asignaciones e invitaciones creadas desde entonces; `users` no cambia):
   ```sql
   BEGIN;
   DROP TABLE IF EXISTS role_assignments, org_invitations;                     -- 026
   DROP TABLE IF EXISTS event_adjustments, evaluation_revisions, evaluations,
     event_registrations, event_staff, event_adjustment_types, event_activities, events;  -- 025
   DROP FUNCTION IF EXISTS evaluation_revisions_immutable();
   COMMIT;
   ```
   Si se creó la fila NTAM sólo para esto y nada cuelga de ella, se puede dejar (inofensiva).
4. **Restaurar el snapshot** `pre-eventos-roles-025-026-…` (Neon → Backup & Restore). Pierde **todo** lo
   escrito en producción después del snapshot (registros, certificados, progreso): último recurso.

## 9. Invitaciones — sólo con confirmación del propietario 🙋

Nada de este runbook envía correos. Las invitaciones de organización (p. ej. **Rubén Soberano como
`ADMIN_ASSOCIATION` de NTAM**, spec §0, decisión 3) se envían **únicamente cuando el propietario lo confirme
explícitamente**, después de que §7 esté verde y el front con `/invitacion` esté publicado:
admin → organización NTAM → «Equipo» → Invitar (o `POST /api/v1/org-nodes/<id NTAM>/invitations`
con `{email, role: "ADMIN_ASSOCIATION"}` como MASTER). El enlace sale como `<FRONTEND_URL>/invitacion?t=…`,
es nominal, de un solo uso y caduca en ≤ 30 días. El padrón NTAM (invitaciones de directores de club,
`--send-emails`) es otra entrega y tampoco se envía sin confirmación.

---

## 10. Ensayo local (2026-09-25)

Sin tocar ningún servicio remoto.

- **Fusión** `origin/main` → `release/eventos`: conflictos en `main.py`, `schemas/user.py`,
  `routers/auth.py`, `routers/users.py`. `/me` usa el `MeResponse` de main y `roles` lo añade
  `ministry_context.me_response`, así `/auth/me`, `/users/me` y `PATCH /users/me/preferences` llevan
  ambos. Main no escribe `users.role` ni membresías.
- **Suite completa** sobre `etl_release` (esquema de `etl_hontr` con 024 + seeds + 025 + 026):
  1203 passed, 2 skipped (pymupdf no instalado).
- **Ensayo de migración** sobre `etl_rehearsal` (esquema tipo producción hasta 024, sin 025/026; 8 usuarios:
  MASTER sin organización, admin de asociación, coordinador de zona, director de club con membresía
  ACTIVE, conquistador con membresía ACTIVE, estudiante sin organización, padre sin organización,
  instructor con membresía ENDED):
  - 025 y 026 con `-1 ON_ERROR_STOP`: OK; backfill `INSERT 0 8`; §3.1 = 0 y 0.
  - Segunda pasada de ambas: `INSERT 0 0`, esquema y filas idénticos (md5).
  - `sync_legacy` + `recompute` sobre cada usuario tras el backfill: sin cambios (la fila BACKFILL del
    director se reutiliza como su rol de club; no se duplica).
  - El instructor con membresía ENDED conserva `INSTRUCTOR` sobre el club (el backfill copia, no corrige):
    por eso la consulta informativa de §2.1.
  - Seed sin NTAM → `ERROR: No existe una asociación con código 'NTAM'` (nada escrito). Tras crear NTAM
    con el SQL de §6: simulacro OK, `--commit` crea el evento (suma base 1000), segunda ejecución
    `created: false`.
  - Humo en proceso contra esa base: health 200; `/auth/me` de MASTER y director con `roles`;
    `/events` como MASTER lista el evento, como director 200 vacío.
  - Hallazgo: un `INSERT` en `organizations` sin `path` deja `path = NULL` (no hay trigger); el SQL de §6
    lo calcula como la app.
