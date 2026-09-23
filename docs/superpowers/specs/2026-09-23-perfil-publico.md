# Perfil público, foto de perfil y XP (bloque G)

Pedido del propietario (2026-09-23, con captura de referencia estilo Unstoppable Domains):
«falta poder subir una foto como foto de perfil, y el perfil de los usuarios. Debe salir su clase,
maestría y procesos. XP (no sé todavía cómo meter algo estilo gamification), sus certificados
emitidos (especialidades ganadas) y especialidades en proceso. La idea es que esta app funcione
como hook para personas que no son miembros y los miembros para una comunidad más online.»

## 1. Reglas que mandan (no negociables)

1. **Menores nunca tienen perfil abierto a internet.** Un usuario con `is_minor` (o sin
   `birth_date` y con tutor) sólo es visible para: él mismo, sus tutores, el staff de su club y
   la jerarquía de su asociación. Sin excepción por «consentimiento» (la visión prohíbe la
   exposición pública de menores).
2. **Adultos: perfil privado por defecto, público por decisión propia** (`profile_visibility`:
   `private` | `club` | `public`). Público = cualquiera con el enlace, sin cuenta.
3. **Nunca se muestran**: correo, fecha de nacimiento, teléfono, ubicación exacta. Sí: nombre,
   foto, portada, biografía corta, club (nombre + ciudad), asociación, clase actual, insignias.
4. Las fotos son medios públicos (bucket público, carpeta `avatars/` y `covers/`), por eso un
   menor **no puede subir portada** y su avatar sólo se ve dentro del alcance del punto 1
   (el archivo sigue siendo público por URL: se aceptan avatares de menores sólo si el tutor lo
   permite → `guardian_allows_avatar`, por defecto falso; hasta entonces se muestra la inicial).
5. Todo cambio de perfil se audita; el `handle` es único, inmutable durante 30 días tras
   cambiarlo, sin palabras reservadas (`admin`, `adventist`, `conquistadores`, roles…).

## 2. Modelo (migración `014_profiles.sql`)

`users` + columnas: `handle` (varchar 32, único, `^[a-z0-9_.]{3,32}$`), `bio` (varchar 280),
`cover_url` (text), `profile_visibility` (varchar 10, default `private`),
`guardian_allows_avatar` (bool default false), `handle_changed_at` (timestamptz).

Sin tablas nuevas: todo lo demás se **deriva**.

## 3. Endpoints

- `GET /api/v1/profiles/{handle_o_id}` → `PublicProfile` (público si `visibility=public` y
  adulto; si no, exige sesión con alcance; 404 en cualquier otro caso — nunca 403, para no
  revelar existencia).
- `GET /api/v1/profiles/me` → el propio, siempre, con `visibility` y los flags.
- `PATCH /api/v1/users/me/profile` → `{name?, handle?, bio?, avatar_url?, cover_url?, profile_visibility?}`
  (validaciones del punto 1 y 5; un menor no puede poner `public` ni `cover_url`).
- `POST /api/v1/media/upload` con `folder=avatars` (ya existe) y nuevo `folder=covers`; el
  frontend recorta en el navegador a 512×512 (avatar) y 1600×600 (portada) antes de subir.
- `PATCH /api/v1/users/{id}` (tutor): `guardian_allows_avatar`.

### `PublicProfile`

```
id, handle, name, avatar_url|null, cover_url|null, bio|null,
club: {id, name, city}|null, association: {id, name}|null,
class: {program_id, name, image_url, progress_pct}|null,        // clase actual (Amigo… Guía)
master_guide: {status: none|in_progress|invested, progress_pct}|null,   // «maestría»
stats: {honors_earned, honors_in_progress, service_hours, attendance},
xp: {total, level, level_name, next_level_at},
badges: [{key, name, description, image_url, earned_at}],       // insignias derivadas
honors_earned: [{honor_id, name, image_url, certificate_no, issued_date, verify_url}],
honors_in_progress: [{honor_id, name, image_url, progress_pct}],
visibility, is_me, can_edit
```

## 4. XP (propuesta v1, todo derivado, sin tabla)

| Fuente | XP |
|---|---|
| Requisito aprobado | 10 |
| Especialidad certificada | 100 (+50 si es de nivel 2, +100 nivel 3) |
| Clase investida | 500 |
| Hora de servicio registrada (bloque F) | 5 (máx. 200/mes) |
| Asistencia registrada | 5 |
| Curso completado (examen aprobado) | 50 |

Niveles: 0 Explorador · 250 Rastreador · 750 Excursionista · 1 500 Guía · 3 000 Pionero ·
6 000 Maestro. Se calcula con una consulta agregada cacheada 10 min. Sólo lo ve el propio
usuario, su club y su asociación; en el perfil **público** se muestra el **nivel**, no el número.

### 4.1 Puntos otorgados por el director («barra de buena conducta») — aprobado 2026-09-23

El propietario: «el director puede dar más puntos por buena conducta (que exista la barra de
buena conducta), por ser puntual o algunas otras cosas».

- Tabla `xp_awards` (en la misma migración `014_profiles.sql`): `id`, `user_id`, `club_id`,
  `awarded_by_id`, `category` (`conducta` | `puntualidad` | `uniforme` | `participacion` |
  `servicio` | `otro`), `points` (smallint, −50..+50), `note` (≤200), `occurred_on` (date),
  `created_at`. Nunca se borra: un error se corrige con otro registro negativo (auditable).
- Quién otorga: CLUB_DIRECTOR y CLUB_SECRETARY del club del miembro (y consejero de su unidad
  cuando exista la unidad); sólo a miembros ACTIVOS de su club. Tope: 100 puntos por
  miembro y semana por club (evita inflación); el API responde 409 `xp_weekly_cap`.
- **Barra de buena conducta**: puntuación móvil de las últimas 8 semanas en `conducta` +
  `puntualidad` + `uniforme` (0–100, arranca en 70 y sube/baja con los premios/penalizaciones).
  Sólo la ve el miembro, sus tutores y el staff del club; nunca en el perfil público ni en
  rankings entre personas. Los puntos positivos sí suman al XP total.
- Endpoints: `POST /api/v1/clubs/{club_id}/members/{membership_id}/xp` (crear premio),
  `GET /api/v1/clubs/{club_id}/xp?week=` (resumen por miembro para el director),
  `GET /api/v1/profiles/me/xp` (historial propio con la barra). Notificación de progreso
  opcional al tutor cuando el miembro es menor (respeta `notify_progress`).
- UI: en el roster del club, en cada miembro, botón «Puntos» → hoja con categorías como
  chips, ±, nota y fecha; en el portafolio propio, tarjeta «Conducta» con la barra y el
  historial. Ranking por **unidad/club** (equipo) en el panel del director, no por persona.

## 5. Insignias derivadas (v1)

`primera-especialidad`, `cinco-especialidades`, `diez-especialidades`, `categoria-completa:<slug>`
(todas las especialidades de una categoría), `clase-<slug>` (investidura), `guia-mayor`,
`100-horas-servicio`, `curso-en-linea` (primer curso virtual aprobado), `fundador` (cuenta
creada antes del lanzamiento público). Imagen = parche o icono en `media.adventist.club/badges/`.

## 6. Frontend

- `/u/[handle]` (público, SSR, `noindex` salvo `public`): portada, avatar, nombre + `@handle`,
  bio, chips de stats (N especialidades · Clase · Club), «Insignias N» en cuadrícula con los
  parches (mismo `HonorImage`), «En proceso», «Certificados» (enlace a `/verify/{no}`), botón
  Compartir (Web Share + copiar enlace). Sin seguidores/likes en v1 (menores).
- `/profile` (propio): foto (cámara/galería, recorte en el navegador, mismo re-encode que la
  evidencia), portada, bio, handle, visibilidad con explicación; vista previa «cómo te ven».
- Menú: avatar en la app bar → `/profile`. En el club (roster) y en el curso, tocar a una
  persona abre su perfil dentro del alcance.
- Specimens en `/styleguide#perfil`.

## 7. Fuera de v1 (ideas aceptadas para después)

Seguidores/siguiendo, mensajería, ranking de fans (vetado para menores), tabla de líderes por
club/unidad (equipo, no individuo), tienda de parches (sevenpxs.com), retos semanales.
