# Revisión de seguridad previa al lanzamiento — septiembre 2026

Alcance: API FastAPI (`backend/app`, base `8972231`) y web Next.js 16 (`conquistadores-app`, base `5b0ba59`).
Prioridad: autorización y privacidad de **menores** (Conquistadores de 10–15 años).
Método: lectura de código de extremo a extremo (router → dependencia → consulta), prueba que falla antes de cada
corrección y la suite completa después. Nada se probó contra producción.

Ramas: `fix/security-review` en los dos repositorios (sin push).
Pruebas del API: 828 → **840 pasan** (`tests/test_security_review.py` y regresiones SEC-xx en
`test_exam_attempts.py`, `test_membership.py`, `test_leader_verification.py`). Web: `npm test` (nuevo,
`node --test tests/**/*.test.mjs`), `tsc`, `eslint` y `next build` en verde.

## Resumen

| Severidad | Total | Corregidos | Decisión del dueño / aceptados |
|---|---|---|---|
| Crítica | 1 | 1 | — |
| Alta | 3 | 2 | 1 (actualizar dependencias web) |
| Media | 13 | 9 | 4 |
| Baja | 16 | 1 | 15 |
| Info | 1 | — | 1 |

## Hallazgos

Evidencia = archivo:línea **antes** de la corrección (API en `backend/`, web en la raíz de `conquistadores-app`).

| ID | Sev. | Componente | Evidencia | Hallazgo | Estado | Commit |
|---|---|---|---|---|---|---|
| SEC-01 | Crítica | Tutorías | `app/routers/users.py:141,248-258` | Cualquier adulto declaraba una tutoría (`POST /users/guardianships`) y la **aprobaba él mismo** (`/approve` sólo comprobaba `guardian_id == yo`). Con ella: portafolio y fotos de evidencia del menor, perfil privado, `guardian_allows_avatar`, decidir o revocar sus membresías y recibir sus correos. | Corregido: aprobar sólo vía enlace de consentimiento (`memberships.decide_consent`); declarar exige correo verificado; retirarse sigue. Panel sin botón «Aceptar». | API `28b8b9b`, web `4d4446d` |
| SEC-03 | Alta | Certificados | `app/main.py:114-155` | `POST /certificates/prototype-batch` no pide sesión y emite certificados que `/certificates/verify` y `/verify/[no]` presentaban «Verificado»: falsificación con QR válido. Sin límite de frecuencia; crea filas `honors` (DRAFT) y `clubs` con nombres arbitrarios. | Mitigado: la verificación devuelve `official` (sólo con `issued_by_id`/`user_id`/`enrollment_id`) y la web muestra «No oficial»; tamaño ≤ 48 in, ≤ 500 imágenes en `/printing/pdf`, límites 30/h y 60/h. **Dueño:** ¿se cierra la herramienta abierta o se exige sesión? | API `a92a555`, web `4d4446d` |
| SEC-04 | Alta | Exámenes | `app/services/exams.py:248,329,765` | Intentos, preguntas vistas y acceso a la clave de respuestas contaban **por inscripción**: retirarse (`DELETE /portfolio/enrollments/{id}`) y volver a unirse daba intentos nuevos, mientras los agotados enseñaban `correct_answer`/`explanation`. | Corregido: cuentan por `(user_id, course_id)`. | API `62546e3` |
| DEP-01 | Alta | Dependencias web | `package.json:28-29`, `npm audit --omit=dev` | 2 críticas + 3 altas: `next 16.0.10` (DoS por deserialización RSC y por el optimizador de imágenes; arrastra `postcss`, `sharp`), `maplibre-gl 5.24` (bypass del sanitizador XSS), `nanoid`. | **Dueño:** subir `next` a ≥ 16.3.6 (no mayor) y `maplibre-gl` a 6.x (mayor; sólo lo usa el especimen de globo). Sólo reportado. | — |
| SEC-02 | Media | Tutorías | `app/rbac.py:452,753` | La tutoría no terminaba a los 18: el ex tutor seguía leyendo portafolio, registros de actividad y perfil privado de un adulto. | Corregido: la rama del tutor exige que la persona siga siendo menor. | API `b8f1126` |
| SEC-05 | Media | Exámenes | `app/services/exams.py:799` | El tutor recibía `correct_answer` de un intento suspendido con intentos restantes (y podía pasárselas al menor). | Corregido: sólo cuando el propio miembro podría verlas. | API `62546e3` |
| SEC-06 | Media | Invitaciones | `app/services/invitations.py:232`, `app/routers/auth.py:182-192` | La invitación nominal de personal (INSTRUCTOR/SECRETARY) comprueba el texto del correo pero no que esté **verificado**: quien obtenga el enlace (p. ej. reenviado por WhatsApp) se registra con ese correo y entra como personal activo con acceso al plantel de menores. | **Dueño:** exigir `VERIFIED` para aceptar invitaciones nominales cambia el flujo de `/join` (registrarse, verificar, volver al enlace). Recomendado antes del lanzamiento. | — |
| SEC-07 | Media | Plantel | `app/routers/clubs.py:114` | `GET /clubs/{id}/members?status=PENDING_CONSENT\|ENDED\|…` mostraba al personal menores sin consentimiento o con el consentimiento retirado (nombre, edad, correo del tutor). | Corregido: el personal del club sólo lista `ACTIVE` y `PENDING_APPROVAL`; el historial queda para la jerarquía. | API `af0223c` |
| SEC-08 | Media | Usuarios | `app/routers/users.py:402` | `PATCH /users/{id}` guardaba cualquier `avatar_url` (píxel de seguimiento de terceros cargado por quien ve plantel o perfil, menores incluidos). | Corregido: sólo archivos `avatars/` del bucket público, como `PATCH /users/me/profile`. | API `267f789` |
| SEC-09 | Media | MFA | `app/routers/auth.py:421,427` | `mfa/verify` contaba fallos pero nunca bloqueaba; el temp token sirve 5 min y el límite por IP se salta (SEC-12): con la contraseña se podía adivinar el TOTP de un MASTER_GC. | Corregido: 5 fallos/hora por cuenta → 429. | API `94c1590` |
| SEC-10 | Media | Render | `app/routers/render.py:70` | `POST /certificates/render` abierto, CPU intensivo (hasta 7200 px) en una instancia de 0,15 CPU, sin límite: DoS trivial. | Corregido: 30/min por cliente, tope de campos/imágenes; la web cae al render local con 429. | API `2e5fc1c`, web `94df18c` |
| SEC-11 | Media | Usuarios (E7) | `app/routers/users.py:350` | Con la verificación de líderes activada, `GET /users` devolvía correo y fecha de nacimiento de los menores a un instructor sin carta vigente, aunque `GET /users/{id}` se lo negaba. | Corregido: mismo filtro que `can_view_user`. | API `35ff0e4` |
| SEC-12 | Media | Límites de frecuencia | `app/rate_limit.py:24-28`; web `app/api/v1/[...path]/route.ts:126-129`, `lib/api/server.ts:90-96` | La IP del límite es el **primer** salto de `X-Forwarded-For` (lo elige el cliente): login, registro y olvido de contraseña no están limitados de verdad. Además la web no reenvía la IP del usuario: todo login pasa por la IP de salida de Vercel y comparte cubo (5/min) → bloqueo global del login en el lanzamiento. | **Dueño/infra:** tomar la IP del salto que añade el proxy de Render (contando saltos de confianza) y reenviar desde la web la IP del cliente en una cabecera firmada con secreto compartido. | — |
| SEC-13 | Media | Sesiones | `app/routers/auth.py:298-307`; web `app/api/session/logout/route.ts:6-12` | JWT sin estado: refresh de 7 días sin rotación ni revocación; cerrar sesión o restablecer la contraseña no invalida los tokens ya emitidos. | **Dueño:** `users.token_version` (o `password_changed_at`) comprobado en `get_authenticated_user` y en `/refresh`, y endpoint de logout. Requiere migración. | — |
| SEC-14 | Media | MFA | `app/config.py:33` | `MASTER_MFA_ENFORCED=False` por defecto: el MASTER_GC opera sin segundo factor. | **Dueño:** inscribir el MFA de cada MASTER_GC y activar la variable en Render antes de abrir. | — |
| FE-01 | Media | Web: login | `lib/safe-next.ts:5` | Redirección abierta: `/auth/login?next=/%09/evil.com` pasaba `safeNext` y el parser de URL la convertía en `//evil.com`. | Corregido: fuera caracteres de control y comprobación de mismo origen. | web `520b2ba` |
| FE-02 | Media | Web: cabeceras | `next.config.ts:1-27` | Sin `frame-ancestors`/`X-Frame-Options`: clickjacking de las pantallas de revisión y administración. | Corregido: `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`. CSP completa pendiente. | web `520b2ba` |
| FE-03 | Baja | Web: proxy | `app/api/v1/[...path]/route.ts:26,59,76,130` | `POST /api/v1/auth/login%2F` esquivaba `DENIED_PATHS`, el API redirigía la barra final y `fetch` la seguía: tokens devueltos a JavaScript (requiere tener ya la credencial). | Corregido: segmentos con `/`, `\` o control rechazados; `redirect: "manual"`. | web `520b2ba` |
| FE-04 | Baja | Web: cookies | `lib/api/server.ts:46` | `cq_rt` con `Path=/` (bastaría `/api`). | Aceptado. | — |
| SEC-15 | Baja | Cartas de iglesia | `app/services/church_letters.py:446,503` | Una carta autorizada tras un traslado verifica a la persona en su club nuevo. | Pendiente (sólo con E7 activo): escribir la verificación sólo si la organización coincide. | — |
| SEC-16 | Baja | Mapas | `app/routers/maps.py:65` | Origen desconocido → token MapKit sin `origin` (cuota del dueño). Deliberado según `tests/test_maps.py:59`. | Aceptado. | — |
| SEC-17 | Baja | Fichas PDF | `app/routers/honor_sheets.py:75` | La clave R2 usa el `locale` crudo: cada variante (`es-aa`, `es-ab`…) fuerza un render y un objeto nuevo. | Pendiente: clave con el locale resuelto. | — |
| SEC-18 | Baja | Especialidades | `app/schemas/honor.py:119,141` | `thumbnail_url`/`image_url` sin validar dominio (los cursos sí). | Pendiente: validador `media_url`. | — |
| SEC-19 | Baja | Medios | `app/services/storage.py:75` | El filtro SVG desescapa una sola vez (doble codificación). Sólo MASTER_GC sube SVG. | Aceptado; recomendado `Content-Security-Policy: sandbox` en el dominio de medios. | — |
| SEC-20 | Baja | Cola de revisión (E7) | `app/services/portfolio.py:919-931` | La cola lista nombre e id de menores a personal sin carta vigente (el detalle sí se niega). | Pendiente (sólo con E7 activo). | — |
| SEC-21 | Baja | Registro | `app/routers/auth.py:143` | `409 Email already registered` permite enumerar cuentas. | Aceptado (el login y el olvido no enumeran). | — |
| SEC-22 | Baja | MFA | `app/routers/auth.py:455` | `mfa/disable` sin código ni contraseña (roles no obligados). | Aceptado. | — |
| SEC-23 | Baja | Avatares de menores | `app/services/storage.py:101` | Retirado el permiso del tutor, el archivo del avatar sigue público en su URL (no adivinable). | **Dueño:** borrar el objeto al retirar el permiso. | — |
| SEC-24 | Baja | Configuración | `app/config.py:25` | `JWT_SECRET` sin longitud mínima. | Pendiente: exigir ≥ 32 bytes en producción. | — |
| SEC-25 | Baja | XP/asistencia | `app/schemas/secretaria.py:87`, `app/services/xp.py:430` | Reuniones retroactivas ilimitadas suman XP y horas; XP negativo sin tope semanal. | Aceptado (integridad, auditado). | — |
| SEC-26 | Baja | Consentimiento | `app/services/memberships.py:610-612` | El menor elige el correo del tutor (límite documentado). | **Dueño**. | — |
| SEC-27 | Baja | Cargos | `app/services/officers.py:86-87` | `include_closed=true` lista a ex miembros. | Pendiente. | — |
| SEC-28 | Baja | Certificados | `app/main.py:120-128` | El lote abierto crea filas `honors` (DRAFT) y `clubs` con nombres arbitrarios. | Ligado a la decisión de SEC-03. | — |
| DEP-02 | Info | Dependencias API | `requirements.txt` | Sin `pip-audit` sin red. Desactualizados relevantes: `starlette 0.47.3`, `fastapi 0.116.1`, `python-multipart 0.0.20`, `PyJWT 2.10.1`. | Pasar `pip-audit` en CI. | — |

## Lo que se revisó y está bien

- **Perfil `/profiles/{handle}`**: un menor nunca es visible para anónimos ni para pares; avatar sólo con permiso del tutor; portada nula; ubicación = ciudad del club, nunca la del miembro (`app/rbac.py:717-765`, `app/services/profiles.py:382-417`).
- **Evidencias**: claves R2 construidas en el servidor (UUID + extensión en lista blanca); URLs prefirmadas de 5 min (GET), 10 min (PUT) y 15 min (álbum), siempre tras `can_view_enrollment`/`can_view_portfolio` y con `no-store` (`app/services/private_storage.py:23-66`).
- **Tokens de invitación y consentimiento**: `token_urlsafe(32)`, sólo SHA-256 en base, sólo en cuerpos JSON, uso único atómico (`UPDATE … RETURNING`), caducidad comprobada al buscar y al gastar (`app/services/invitations.py:151-206`).
- **Escalada por invitación**: el personal sólo se invita nominal y de un uso; los enlaces múltiples sólo llevan STUDENT; nadie otorga un rol que no supera (`app/rbac.py:232-242`).
- **Aislamiento entre clubes**: plantel, unidades, reuniones y cargos se consultan con `club_id`; la jerarquía por `ltree` (`app/rbac.py:215-260`).
- **Exportación CSV**: sin fechas de nacimiento, teléfonos ni direcciones; sin correo de menores; celdas de fórmula neutralizadas; auditada (`app/routers/clubs.py:319-383`).
- **XP**: mismo club, nunca a uno mismo, −50..50, tope semanal, 90 días hacia atrás (`app/services/xp.py:413-431`).
- **Exámenes**: el papel no lleva respuestas; sorteo y barajado en el servidor; plazo con `deadline_at` del servidor; sólo el dueño responde (`app/services/exams.py:105,177-200,436-465`).
- **Especialidades y programas**: borradores 404 para terceros; categorías sólo MASTER_GC; edición en sitio sólo creador en borrador o MASTER_GC (`app/routers/honors.py:733-747,849-990,1191-1262`; `app/services/programs.py:78-100`).
- **Ficha PDF (SSRF)**: sólo https, host exacto del bucket, sin redirecciones, 3 s, tope de tamaño y tipo (`app/services/honor_sheet.py:505-546`); los borradores nunca se suben a R2.
- **Árbol organizativo**: crear/editar/borrar exige el nodo dentro del propio subárbol; un coordinador de zona no crea estructura (`app/routers/org.py:92-100,732-897`).
- **Subida de medios**: carpetas en lista blanca, magic bytes, 10/50 MB, SVG sólo MASTER_GC en `patches`/`logos`, reglas de avatar de menores (`app/routers/media.py:47-137`).
- **JWT**: algoritmo fijado en `decode`, `exp`/`sub` obligatorios, tipo de token comprobado (`app/security.py:201-220`); restablecimiento y verificación de correo de un solo uso, atómicos, con tope de fallos; olvido de contraseña sin enumeración ni diferencia de tiempos.
- **Web**: cookies HttpOnly + SameSite=Lax + Secure en https; los tokens nunca llegan al navegador ni al RSC; el proxy no reenvía cookies, Host ni X-Forwarded-For; control de `Origin` en toda mutación; sin `dangerouslySetInnerHTML` ni server actions; enlaces Markdown sólo http/https; vídeos sólo youtube-nocookie/Vimeo por id; `/api/revalidate/honors` exige POST, mismo origen y sesión con rol.
- **Mensajería adulto–menor**: no existe.

## Decisiones pendientes del dueño (por orden)

1. **SEC-12** límites de frecuencia tras Vercel/Render (bloqueo global del login el día del lanzamiento).
2. **DEP-01** subir `next` a ≥ 16.3.6.
3. **SEC-14** activar `MASTER_MFA_ENFORCED` tras inscribir el MFA de los MASTER_GC.
4. **SEC-06** exigir correo verificado para aceptar invitaciones de personal.
5. **SEC-03/28** futuro de la herramienta abierta de certificados.
6. **SEC-13** revocación de sesiones (migración).
