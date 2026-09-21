# Bloque F — Guías Mayores y Aventureros sobre el mismo motor

Fecha: 22 sep 2026 · Estado: **borrador para aprobación del responsable** (sólo diseño: sin código, sin migraciones aplicadas).
Repos: `adventist.club` (FastAPI, este), `conquistadores.app` (Next.js) y, cuando toque, `guiasmayores.app` y la app de
Aventureros. Todo es aditivo. **F empieza cuando el bloque A está en producción y usado por al menos un club de punta a
punta** (inscripción → dictamen → certificado); nada de lo que sigue se adelanta.

Lectura previa: `2026-09-22-portafolio-design.md` (bloque A), `docs/VISION_ECOSISTEMA.md`, `docs/VISION_ANEXO_MVP.md`,
`docs/I18N_Y_PLANTILLAS.md`, `docs/ESPECIALIDADES_WIKI.md`.

Vocabulario de este documento: **programa** = cualquier currículo con secciones y requisitos que no es una especialidad
(clases de Conquistadores y de Aventureros, Guía Mayor, EMC, CMJA). **Tarjeta** = la vista de un programa de tipo clase.
**Carpeta** = la vista de un programa de Guías Mayores. Las tres palabras son la misma inscripción del motor de A.

## Decisiones para el responsable

El resto del documento está escrito **asumiendo la opción recomendada** de cada una. Cambiar una decisión afecta sólo a
la sección que se indica.

| # | Decisión | Opciones | Recomendación | Consecuencia |
|---|---|---|---|---|
| D1 | Qué sale primero | (a) Clases de Conquistadores dentro de `conquistadores.app`, después GM · (b) GM primero con `guiasmayores.app` · (c) todo a la vez | **(a)**: piloto con la clase *Amigo* | Se prueba el motor generalizado con usuarios y clubes que ya existen, sin frontend nuevo, sin mentor y sin bitácora. Con (b) el orden pasa a F1 → F2 → F4 → F5 y F3 se pospone; (c) no se recomienda: tres frentes con un solo equipo |
| D2 | Qué currículo es «el oficial» | (a) El de la Pathfinder Wiki (DNA/AG) tal cual · (b) Sólo el manual de la División del club (DIA para NTAM), cargado a mano · (c) Importar de la wiki como **borrador** y publicar sólo tras cotejarlo con el manual vigente; campo `authority` visible | **(c)** | Ningún programa aparece al público sin que una persona lo compare con el manual; donde difiera se corrige el JSON antes de publicar. Los textos que no vengan de la wiki necesitan permiso de uso (igual que la nota de licencias de parches en `ESTADO.md`) |
| D3 | Quién emite la investidura de GM / EMC / CMJA | (a) Director del club, como en A · (b) **La Asociación** (`ADMIN_ASSOCIATION` con jurisdicción) · (c) Director solicita y Asociación aprueba (dos pasos) | **(b)** para GM/EMC/CMJA; clases de club siguen con el director | Una columna `programs.issuer_level` (`CLUB` \| `ASSOCIATION`) y una rama en `can_issue`. (c) exige un estado nuevo en la inscripción: no se construye salvo que se pida |
| D4 | Peso del dictamen del mentor | (a) Vale como el de cualquier revisor (Completo / Incompleto) · (b) Provisional: el director lo valida después · (c) El mentor sólo comenta | **(a)** | No hay estados nuevos en `requirement_progress`. «El director valida, la Asociación audita» se cumple porque director y Asociación pueden reabrir cualquier requisito (regla 4 de A) y porque la emisión (D3) es de la Asociación. (b) duplica el trabajo del director en cada requisito |
| D5 | Quién puede ser mentor y quién lo habilita | (a) Cualquier GM que se declare tal · (b) Lo habilita el director del club · (c) Lo habilita **Zona o Asociación**, la misma autoridad que hoy verifica instructores | **(c)** | Mentor = adulto, cuenta `ACTIVE`, `verification_status = VERIFIED`, `child_protection_completed`, investido GM (certificado de la plataforma o fecha atestada por quien habilita). Reutiliza el flujo de verificación existente; perfil de mentor voluntario |
| D6 | Mentoría cuando el aconsejado es menor (16–17 en GM) | (a) No se permite: sólo dictamina el club · (b) Permitida con consentimiento del tutor **y** visto bueno del director, sin ningún canal privado · (c) Igual que con adultos | **(b)** | Dos aprobaciones extra antes de activar; todo lo que el mentor escribe lo leen tutor y director; el mentor nunca recibe datos de contacto del menor |
| D7 | Bitácora: quién la ve y desde qué edad | (a) Nadie más, nunca · (b) Privada; el autor puede compartir entradas sueltas con su mentor; los revisores ven sólo **conteos** si el autor lo autoriza; disponible desde 16 años · (c) Mentor y tutor leen todo | **(b)** | Contenido cifrado en la aplicación; ni MASTER ni la consola de Neon lo leen. Lo que un menor comparte con su mentor también lo ve su tutor (se avisa antes de compartir). Llevarla a menores de 16 es una decisión futura, no de este bloque |
| D8 | Cuentas de Aventureros (4–9 años) | (a) **Perfil gestionado**: lo crea el tutor, sin inicio de sesión; el club registra el avance; el tutor sólo lee · (b) Cuenta propia con correo del tutor · (c) El tutor actúa en nombre del niño | **(a)** | Respeta la regla de la visión «el tutor no modifica evidencias» y el 100 % de consentimiento parental; no se guardan fotos de niños de 4–9 (las clases no exigen evidencia). (c) contradice la visión; (b) obliga a gestionar contraseñas de niños pequeños |

## 0. Principios

1. **Un solo motor.** Inscripción, progreso por requisito, evidencias privadas, dictamen, «listo» y certificado son los de A.
   F añade un catálogo (programas) y tipos de requisito que se completan solos; no añade otro flujo.
2. **Las tablas de A no cambian de forma**: sólo columnas nuevas opcionales o con valor por defecto. Nada se renombra
   (tampoco `honor_enrollments`, aunque el nombre se quede corto: el coste de renombrar no compra nada).
3. **Nada queda atado a un ministerio** (regla de `ESTADO.md`): el ministerio sale siempre del dato (`programs.ministry_id`,
   `honors.ministry_id`), nunca de un valor por defecto escondido ni de lo que diga el cliente.
4. **YAGNI**: cada tabla aparece en el incremento que la usa. La sección 9 lista lo que no se construye antes de tiempo.
5. **Protección infantil primero**: ningún canal privado adulto–menor, todo lo escrito por un adulto sobre un menor lo
   puede leer un segundo adulto, y cada acción queda en `audit_log`.

## 1. La generalización: programas sobre el motor de A

### 1.1 Decisión de arquitectura

Lo único del bloque A que está atado a especialidades es `honor_enrollments.honor_id`. `requirement_progress` ya se
identifica por `(enrollment_id, requirement_position)` y `evidences` cuelga del progreso: son agnósticas.

| Criterio | (1) Generalizar `honors` (`honors.kind = PROGRAM`) | (2) **`programs` paralelo + mismas tablas de progreso y evidencias** | (3) Motor aparte (`program_enrollments`, `program_progress`…) |
|---|---|---|---|
| Coste de migración | 1 columna + secciones + columnas en `honor_requirements`; cero cambios en inscripciones | 6 tablas nuevas; en A: 1 columna y quitar un `NOT NULL` en inscripciones, 3 columnas en progreso, 1 en certificados (todo metadatos, instantáneo) | Duplicar 3 tablas, sus servicios, su router y sus tests |
| Sencillez de consultas | **Todas** las consultas de especialidades necesitan `kind = 'HONOR'`: lista pública, categorías, estadísticas, `my/created`, revisiones pendientes, la búsqueda por nombre de `prototype-batch`, los importadores de la wiki, `catalog-server` del frontend y la futura validación de Sevenpxs | Las consultas de especialidades no se tocan. El motor consulta igual que hoy; sólo el cargador de requisitos tiene dos ramas y los listados hacen `LEFT JOIN` a `honors` y a `programs` | El portafolio pasa a ser un `UNION` de dos motores; la cola de revisión, también |
| Riesgo para producción | Medio-alto: un filtro olvidado mete «Amigo» entre las 809 especialidades o deja comprar un parche con un certificado de clase; el flujo de autoría de instructores permitiría «crear programas»; columnas sin sentido (`exam_*`, `skill_level`, `wiki_title`, `category_id`) | Bajo: las 809 especialidades, sus traducciones, requisitos y certificados no cambian ni de tabla ni de consulta | Bajo en datos, alto en mantenimiento; contradice «reutilizar, no bifurcar» |

**Se elige (2).** Un adaptador en `app/services/curriculum.py` devuelve la lista de requisitos de una inscripción como
`RequirementSpec(position, requirement_id, section, label, text, kind, evidence_required, target)` con dos cargadores
(`load_honor_specs`, `load_program_specs`). `portfolio.enroll()` crea las filas de progreso a partir de esa lista; todo lo
que viene después (envío, evidencias, dictamen, `READY`, certificado, permisos, auditoría) es el código de A sin ramas.
Se descartó también una tabla `program_enrollments` apuntando a `requirement_progress`: obligaría a una FK polimórfica.

### 1.2 Modelo de datos — migración `012_programs.sql` (aditiva, idempotente) · incremento F1

**`programs`**
| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| ministry_id | uuid FK ministries NOT NULL | `pathfinders`, `adventurers`, `master-guides` (los mismos slugs que los emblemas) |
| kind | varchar(12) NOT NULL | `CLASS` (tarjeta de club) \| `CURRICULUM` (GM, EMC, CMJA) |
| slug | varchar(120) NOT NULL | único por `(ministry_id, slug)`; la versión N añade `-vN` como en especialidades |
| code | varchar(40) NULL | único por ministerio cuando existe |
| name, description | varchar(180) NOT NULL, text NULL | texto fuente en español |
| image_url | text NULL | insignia oficial de la clase o del programa (R2 público) |
| sort_order | smallint NOT NULL DEFAULT 0 | Amigo = 1 … Guía = 6 |
| authority | varchar(10) NULL | `GC`, `NAD`, `IAD`, `SAD`…: a qué manual corresponde (D2); se muestra en la ficha |
| status | varchar(30) NOT NULL DEFAULT 'DRAFT' | `DRAFT` → `PUBLISHED` → `ARCHIVED`. Sin revisión zonal: un currículo oficial no lo escribe un instructor |
| version, previous_version_id, published_at | int DEFAULT 1, uuid FK programs NULL, timestamptz | mismo versionado que `honors`: publicado = inmutable; los cambios crean versión nueva y archivan la anterior |
| source, source_url, license | varchar(40), text, varchar(40) | atribución (CC BY-SA cuando viene de la wiki) |
| created_by_id, created_at, updated_at | | |

**`program_translations`** `(program_id, locale)` PK → `name`, `description`, `source`, `source_url`, `license`. Los nombres de
las clases cambian por territorio (*Orientador* / *Pioneiro* / *Ranger*): salen de aquí, nunca del código.

**`program_sections`** `id`, `program_id` FK CASCADE, `position` int, `slug` varchar(80), `name` varchar(180); únicos
`(program_id, position)` y `(program_id, slug)`. **`program_section_translations`** `(section_id, locale)` → `name`.
Las secciones son datos del programa, no un enum: «Liderazgo, Estilo de vida, Crecimiento espiritual, Servicio comunitario,
Share Your Faith» son simplemente las cinco filas del programa que las use.

**`program_requirements`** — la **estructura**, una fila por requisito, igual en todos los idiomas
| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| program_id, section_id | uuid FK | |
| position | int NOT NULL | **global dentro del programa** (1…N cruzando secciones): es la `requirement_position` de A. Único `(program_id, position)` |
| label | varchar(12) NOT NULL | lo que se muestra: «I.3», «4» |
| kind | varchar(8) NOT NULL DEFAULT 'FREE' | `FREE` \| `HONOR` \| `PROGRAM` \| `HOURS` (F2); `COURSE` queda reservado para F9 |
| evidence_required | boolean NOT NULL DEFAULT false | se copia a `requirement_progress.is_practical` al inscribirse (regla 1 de A); en `HONOR` y `PROGRAM` la copia es siempre `true` y en `HOURS` siempre `false` (ver 1.3) |
| target_honor_id | uuid FK honors NULL | `HONOR`: especialidad concreta (cualquier versión de su linaje) |
| target_category_id | uuid FK honor_categories NULL | `HONOR` abierto: «una especialidad de Naturaleza»; ambos NULL = «una especialidad a elección» |
| target_program_id | uuid FK programs NULL | `PROGRAM`: «estar investido de Amigo», «haber completado el EMC» |
| target_quantity, activity_category | numeric(5,1) NULL, varchar(12) NULL | `HOURS`: cuánto y de qué (`SERVICE` en horas, `ATTENDANCE` en reuniones) |

`CHECK`: los campos `target_*` sólo se rellenan con el `kind` que les corresponde.

**`program_requirement_texts`** `(requirement_id, locale)` PK → `description` text, `instructions` text NULL, `source`,
`source_url`, `license`. Se separa estructura y texto (a diferencia de `honor_requirements`, que repite la fila por
idioma) porque aquí la fila tiene tipo y destino, y eso no puede diferir entre idiomas.

**Cambios en tablas de A** (todos de metadatos; ninguna fila existente cambia de significado)
- `honor_enrollments`: `program_id uuid FK programs NULL`; `honor_id` pasa a admitir NULL;
  `CHECK (num_nonnulls(honor_id, program_id) = 1)`; índice único parcial `(user_id, program_id) WHERE status <> 'WITHDRAWN'
  AND program_id IS NOT NULL`; índice `(program_id, status)`. El índice único de especialidades sigue igual.
- `requirement_progress`: `kind varchar(8) NOT NULL DEFAULT 'FREE'` (copia del tipo al inscribirse, como `is_practical`);
  `program_requirement_id uuid FK program_requirements NULL ON DELETE SET NULL` (gemela informativa de `requirement_id`);
  `satisfied_by_enrollment_id uuid FK honor_enrollments NULL` con índice único parcial
  `(enrollment_id, satisfied_by_enrollment_id)`: un mismo logro no completa dos requisitos de la misma inscripción.
  `completed_via` admite además `HONOR`, `PROGRAM`, `HOURS` (y `COURSE` en F9); caben en `varchar(8)`.
- `certificates`: `program_id uuid FK programs NULL`. `honor_name_snapshot` guarda el nombre del programa (es «lo que se
  obtuvo»); `honor_id` queda NULL. **Sólo los certificados con `honor_id` habilitan la compra de parche** en Sevenpxs.
- Semilla idempotente de `ministries` para `adventurers` y `master-guides` si no existen.

Vuelta atrás: mientras no haya inscripciones de programa, `012` se revierte borrando las tablas nuevas y reponiendo el
`NOT NULL`; después ya no (se documenta en la cabecera del SQL).

### 1.3 Cómo se completa cada tipo de requisito

| kind | Cómo se completa | `completed_via` | Incremento |
|---|---|---|---|
| `FREE` | Flujo de A: nota + evidencias (obligatoria si `evidence_required`) → `SUBMITTED` → dictamen | `REVIEW` | F1 |
| `HONOR` con destino concreto | **Automático**: el miembro tiene una inscripción `CERTIFIED` de cualquier versión de esa especialidad | `HONOR` | F2 |
| `HONOR` abierto (categoría o libre) | El miembro **elige** cuál de sus especialidades certificadas aplica; el servicio valida categoría y que no esté usada ya en esa inscripción | `HONOR` | F2 |
| `PROGRAM` | Automático: inscripción `CERTIFIED` en cualquier versión del programa destino | `PROGRAM` | F2 |
| `HOURS` | Automático: la suma de actividades **aprobadas por el director** alcanza `target_quantity` | `HOURS` | F2 |
| `COURSE` | Curso o examen aprobado de los bloques B–D | `COURSE` / `EXAM` | F9 |

Un único punto de entrada, `portfolio.auto_complete(progress, via, source)`, que usan los tres automáticos de hoy y usarán
B–D: marca `COMPLETE`, deja `reviewed_by_id` NULL, guarda el origen, recalcula `READY` (regla 2 de A) y audita
`REQUIREMENT_AUTOCOMPLETE`. `portfolio_links.sync(db, user_id)` lo invoca, de forma idempotente y en la misma
transacción, en tres momentos: al inscribirse en un programa, al emitir cualquier certificado del usuario y al decidir una
actividad.

**Logros obtenidos fuera de la plataforma** (especialidades en papel, investiduras anteriores): los certificados antiguos
no están ligados a una cuenta y no cuentan solos. El miembro envía el requisito con evidencia (foto del certificado o de
la banda) y un revisor lo dictamina: `completed_via = REVIEW`. Por eso las filas `HONOR` y `PROGRAM` nacen con
`is_practical = true`: la vía manual exige evidencia; la automática no pasa por la regla 1, igual que `EXAM`.

**Linaje de versiones**: `honor_lineage_ids(db, honor_id)` y `program_lineage_ids(...)` recorren `previous_version_id` en
ambos sentidos (CTE recursiva; las cadenas son de 1–3 filas). Un requisito que apunta a «Nudos v1» se cumple con «Nudos v2».

#### Actividades (horas de servicio y asistencia) — migración `013_activity_logs.sql` · F2

**`activity_logs`**: `id`, `user_id` FK, `club_id` FK organizations (club del miembro al registrarla), `category`
varchar(12) (`SERVICE` \| `ATTENDANCE`), `performed_on` date, `quantity` numeric(4,1) (`> 0`; horas ≤ 24; asistencia = 1),
`description` varchar(500), `place` varchar(180) NULL, `status` (`SUBMITTED` → `APPROVED` \| `REJECTED`; `APPROVED` →
`REJECTED` con nota), `created_by_id`, `decided_by_id`, `decided_at`, `decision_note` varchar(500), `created_at`.
Índices `(user_id, category, status)` y `(club_id, status)`.
- La registra el miembro para sí, o el **director para varios miembros a la vez** («el club hizo 3 h el sábado»): la
  visión dice que las horas las asigna y las aprueba el director. Las que crea el director nacen `APPROVED`.
- Aprueba `can_approve_activity`: `CLUB_DIRECTOR` (aprobado, no bloqueado) del club actual del miembro, o `MASTER_GC`.
  Las del propio director las aprueba Zona o Asociación con jurisdicción. El instructor **no** aprueba horas.
- Cuentan para un requisito `HOURS` las aprobadas de su categoría con `performed_on ≥` fecha de inicio de la inscripción.
  Si una aprobada pasa a rechazada y la suma baja, el requisito vuelve a `PENDING` (salvo inscripción congelada).

### 1.4 Reglas de integridad añadidas (las cinco de A siguen vigentes)

6. Una inscripción es de una especialidad **o** de un programa, nunca de ambos ni de ninguno (CHECK + validación 422).
7. La inscripción fija versión del programa e idioma; se rechaza con 409 si el programa no tiene requisitos en ningún idioma.
8. Un requisito `HONOR` o `PROGRAM` no admite dictamen `COMPLETE` manual **sin evidencia**; con evidencia sí (vía «fuera de
   la plataforma»). Un requisito `HOURS` no admite dictamen `COMPLETE` manual nunca (409 «Registra y aprueba las horas»): su
   única vía es el registro de actividades. Dictaminar `INCOMPLETE` uno completado automáticamente rompe el vínculo
   (`satisfied_by_enrollment_id = NULL`) y exige nota.
9. Un logro completa como mucho un requisito por inscripción (índice único); los de destino concreto se resuelven antes
   que los abiertos para no «gastar» la especialidad de Naturaleza en el hueco libre.
10. Los prerrequisitos del programa (F4) no bloquean inscribirse ni avanzar: bloquean **emitir** (409 con la lista).
11. Cuando exista la anulación de certificados por fraude, anular uno debe llamar a `portfolio_links.unlink`: los requisitos
    que completó vuelven a `PENDING` si la inscripción no está certificada; si lo está, se marca para auditoría.

### 1.5 Permisos (`app/rbac.py`)

- `can_publish_program(actor)`: `MASTER_GC`. (Variantes territoriales publicadas por una Asociación: fuera de alcance.)
- `can_review(db, actor, enrollment)` gana dos ramas, **sólo aquí**: mentor asignado y activo de esa inscripción (F5) y,
  para programas con `issuer_level = 'ASSOCIATION'`, `ADMIN_ASSOCIATION` / `COORDINATOR_ZONE` con el club del miembro en
  su jurisdicción (`club_scope_paths`) (F4). Esto último resuelve el caso más común en GM: **el director que es a la vez
  aspirante** no puede dictaminarse (regla 5) y necesita un revisor por encima.
- `can_issue`: según `issuer_level` — `CLUB`: como en A; `ASSOCIATION`: `ADMIN_ASSOCIATION` con jurisdicción o `MASTER_GC` (F4).
- `can_view_enrollment(db, actor, enrollment)` = `can_view_portfolio(dueño)` **o** mentor activo de esa inscripción. Sustituye
  a `can_view_portfolio` en `GET /enrollments/{id}` y `GET /evidences/{id}/url`. El mentor **no** entra en
  `can_view_portfolio`: ve una carpeta, no el portafolio.
- `can_approve_activity`, `can_enroll_member` y `can_bulk_sign` (F3) = jurisdicción de club de `can_review`, sin la rama de mentor.
- Si el bloque E (migración 008) cambia cómo se sabe «el club actual del miembro», el único punto que se toca es el
  auxiliar que ya usa `can_review`.

### 1.6 API

**`app/routers/programs.py`, prefijo `/api/v1/programs`**
| Método y ruta | Quién | Qué |
|---|---|---|
| `GET /programs?ministry=&kind=&locale=` | público | Programas `PUBLISHED` del ministerio (`ministry` **obligatorio**: sin valor por defecto), con nombre en el idioma, insignia, `sort_order`, nº de requisitos |
| `GET /programs/{id}?locale=` | público (no publicados: `MASTER_GC`) | Secciones → requisitos (`label`, texto, `kind`, `evidence_required`, destino resumido: especialidad con su parche, programa o cantidad), atribución por fila. Misma resolución de idioma que `/honors/{id}` |
| `POST /programs/{id}/publish` · `POST /programs/{id}/archive` | `can_publish_program` | 409 si no hay requisitos o si un `target_*` apunta a algo no publicado |

No hay alta ni edición por API: los programas entran por el importador (sección 7).

**Cambios en `/api/v1/portfolio`** (contrato de A intacto para especialidades)
| Método y ruta | Cambio |
|---|---|
| `POST /enrollments` | Acepta `{program_id, locale?}` **o** `{honor_id, locale?}`; 422 si vienen ambos o ninguno. Tras crear, ejecuta `portfolio_links.sync` |
| `GET /enrollments?status=&ministry=&type=honor\|program` | Cada elemento lleva `type`, `ministry` y, si es programa, `program {id, slug, name, kind, image_url}` |
| `GET /enrollments/{id}` | Si es programa: `sections[]` con sus requisitos; cada requisito añade `kind`, `label`, `target`, `satisfied_by {type, name, certificate_no}` y, en `HOURS`, `quantity {approved, target}`. Permiso: `can_view_enrollment` |
| `PUT /enrollments/{id}/requirements/{position}` | En `HONOR` abierto acepta `{honor_enrollment_id}`: valida dueño, `CERTIFIED`, categoría y regla 9 ⇒ `COMPLETE` |
| `GET /review/queue` | Incluye inscripciones de programa de la jurisdicción del actor (club, Asociación en F4, aconsejados en F5) |
| `POST /enrollments/{id}/certificate` | Para programas: plantilla de tipo `program` del ministerio; valida prerrequisitos (F4) |
| `GET /users/{id}` · `GET /me` | Agrupado por ministerio; acepta `?ministry=` |

**`app/routers/activity.py`, prefijo `/api/v1/activity`** (F2)
| Método y ruta | Quién | Qué |
|---|---|---|
| `POST /logs` `{category, performed_on, quantity, description, place?, user_ids?}` | miembro (sin `user_ids`) · director (`user_ids` de su club, ≤ 60) | Crea `SUBMITTED` (miembro) o `APPROVED` (director); fecha futura ⇒ 422 |
| `GET /logs?user_id=&category=&status=` | dueño · `can_view_portfolio` | Lista con totales aprobados por categoría |
| `GET /queue` | `can_approve_activity` | Pendientes de su club |
| `POST /logs/{id}/decision` `{status: APPROVED\|REJECTED, note?}` | `can_approve_activity`, nunca el propio miembro | Nota obligatoria al rechazar; dispara `portfolio_links.sync` |
| `DELETE /logs/{id}` | dueño, sólo `SUBMITTED` | |

### 1.7 Certificado de programa y plantillas

- Emisión: la misma `issue_certificate(...)` de A, con `program_id`, `ministry_id` del programa y `honor_name_snapshot` =
  nombre del programa en el idioma del certificado. La verificación pública añade `kind: "honor" | "program"` para que cada
  frontend elija el rótulo.
- Plantillas: cada carpeta `templates/certificates/<slug>/` admite un `meta.json` opcional
  `{"ministries": ["master-guides"], "kinds": ["program"]}`; sin `meta.json` = todos los ministerios, tipo `honor`
  (compatibilidad). `GET /certificates/templates?ministry=&kind=` filtra. El contrato de ids no cambia: el nombre del
  programa va en `honor_name`, y el emblema por ministerio ya lo resuelve `default_emblem(ministry)`.
- Plantillas a diseñar por el responsable con `tools/design_to_template.py`: `investidura-clase` (Conquistadores),
  `investidura-guia-mayor` (GM/EMC/CMJA) y `investidura-aventureros`. Sin plantilla de tipo `program` para el ministerio,
  la emisión responde 409 «No hay plantilla de investidura para este ministerio».

### 1.8 Trabajo del club sobre la tarjeta · F3 (sin migración)

En un club real el consejero firma la tarjeta requisito a requisito, muchas veces a toda la unidad y a niños sin teléfono.
- `POST /portfolio/club/enrollments` `{program_id, user_ids[] ≤ 60}` — `can_enroll_member`: inscribe a miembros de su club
  (idempotente por miembro; el miembro la ve y puede retirarse). Auditoría `ENROLL` con `on_behalf: true`.
- `POST /portfolio/review/bulk` `{program_id, position, user_ids[] ≤ 60, note?}` — `can_bulk_sign`: **firma directa**
  `PENDING | SUBMITTED | INCOMPLETE → COMPLETE` con `completed_via = REVIEW`. Sólo en programas `kind = CLASS`, requisitos
  `FREE` con `evidence_required = false`. Excluye al propio actor; responde por miembro `{user_id, result, reason}`; una
  transacción; **una fila de auditoría por miembro** (`REQUIREMENT_REVIEW`, con `bulk_id`) para que el rastro de cada menor
  esté completo. Es la única excepción a la máquina de estados de A y no aplica a especialidades ni a carpetas de GM.
- `GET /portfolio/club/programs/{program_id}/matrix` — miembros × requisitos con estado, para la pantalla de firma.

### 1.9 Frontend de F1–F3 (`conquistadores.app`, kit `cq-*`)

- **`/classes`**: las seis clases con su insignia, anillo de avance y estado (sin empezar / en curso / lista / investida).
  **`/classes/[id]`**: tarjeta digital — secciones en acordeón con contador, requisitos con `RequirementProgress` de A;
  los `HONOR` muestran el parche y enlazan a la especialidad («Ya la tienes» o «Empezar»); los `HOURS`, una barra con
  lo aprobado; invitado = lista de sólo lectura con «Inicia sesión para guardar tu avance». Atribución CC BY-SA al pie.
- **`/activity`**: mis horas (alta, estado, totales). Panel del director: **Horas por aprobar** y alta para varios miembros.
- **`/portfolio`**: grupo «Clases» junto a las especialidades; los certificados de investidura con su verificación.
- **Panel del director / instructor**: **Tarjetas del club** → matriz miembros × requisitos con selección múltiple y hoja
  **Firmar requisito**; **Inscribir al club en una clase**.
- Las carpetas dinámicas hermanas usan el mismo nombre de parámetro (`[id]`): ya costó un 500 general (`ESTADO.md`).
- Kit nuevo (con entrada en `/styleguide` y skeleton): `ProgramCard`, `SectionAccordion`, `RequirementTargetChip`,
  `QuantityMeter`, `MemberMatrix`, `BulkSignSheet`, `ActivityLogRow`.

### 1.10 Privacidad, auditoría, pruebas, despliegue

- Menores: mismas garantías de A (bucket privado, URL firmada de 5 min, recodificado sin EXIF). La matriz del club muestra
  sólo nombre y estado. `activity_logs.description` y `place` se ven con `can_view_portfolio`, nada público.
- Auditoría nueva: `PROGRAM_IMPORT`, `PROGRAM_PUBLISH`, `PROGRAM_ARCHIVE`, `REQUIREMENT_AUTOCOMPLETE`, `REQUIREMENT_LINK`,
  `REQUIREMENT_UNLINK`, `ACTIVITY_LOG_CREATE`, `ACTIVITY_LOG_DECIDE`, `ACTIVITY_LOG_DELETE`; `ENROLL` y `REQUIREMENT_REVIEW`
  añaden `program_id`, `on_behalf`, `bulk_id` en `metadata_json`.
- Pruebas (pytest de integración, TDD): **toda la batería de A sigue verde sin tocarla** (es la prueba de que no se bifurcó);
  CHECK «uno u otro»; inscripción de programa fija versión e idioma y crea N filas con `kind` y `is_practical` correctos;
  detalle agrupado por secciones en `es` y `en` con las mismas posiciones; `HONOR` concreto se completa al inscribirse (ya
  certificado) y al certificar después; linaje v1/v2; abierto por categoría rechaza categoría distinta y logro ya usado;
  vía manual sin evidencia ⇒ 409; `INCOMPLETE` sobre automático rompe el vínculo; `HOURS` sube y baja con aprobaciones;
  el director no aprueba sus horas; el instructor no aprueba horas; firma en bloque sólo `CLASS` + `FREE` sin evidencia, excluye
  al actor, audita por miembro; emisión de programa crea certificado con `program_id` y sin `honor_id`; `/honors`,
  categorías y estadísticas devuelven **exactamente** lo mismo que antes de `012` (test de no regresión del catálogo).
- Frontend: `tsc`, `eslint`, `next build`, ampliación de `scripts/e2e-auth.mjs` (inscribirse en Amigo, enviar, firma en bloque,
  especialidad que completa un requisito, emitir) y revisión a 375, 768, 1280 y 1920 px en claro y oscuro.
- Despliegue de cada incremento: migración en Neon **antes** del backend; backend; frontend. Se despliega «a oscuras»: un
  programa no existe para el público hasta que se publica, así que publicar *Amigo* es el interruptor. Piloto con un club.

## 2. Superficie multi-ministerio · F4

### 2.1 Ministerios, aplicaciones y dominios

| `applications.slug` | `ministry` | Dominio | Cuándo |
|---|---|---|---|
| `conquistadores` | `pathfinders` | `conquistadores.app` | hoy |
| `guiasmayores` | `master-guides` | `guiasmayores.app` | F4 |
| `aventureros` | `adventurers` | por definir por el responsable | F8 |

| Compartido (una sola vez en el CORE) | Por ministerio |
|---|---|
| Cuentas, contraseñas, 2FA, tutelas y consentimientos | Catálogos: especialidades (`honors.ministry_id`), categorías, programas |
| Árbol de organizaciones, clubes y membresía | Plantillas de certificado (`meta.json`) y emblema oficial |
| Portafolio: inscripciones, progreso, evidencias, actividades, revisiones | Terminología e identidad visual del frontend |
| Certificados, folios, verificación pública, auditoría | Navegación y pantallas propias (carpeta, mentor, bitácora) |

Un aspirante a GM gana especialidades del catálogo de **Conquistadores**: `guiasmayores.app` muestra `/honors` con
`ministry=pathfinders` y los requisitos `HONOR` de la carpeta apuntan a esas mismas filas. Las especialidades de
Aventureros son filas de `honors` con su propio `ministry_id`: el modelo actual ya lo admite, sólo hay que importarlas.

### 2.2 Cómo viaja `ministry` por el API

1. **Catálogos**: `?ministry=<slug>` explícito, como `locale`. `/honors` conserva su valor por defecto `pathfinders` por
   compatibilidad; los endpoints nuevos lo exigen.
2. **Datos personales** (`/portfolio/me`, `/enrollments`, `/activity/logs`): devuelven todo, cada elemento con su `ministry`;
   `?ministry=` es sólo un filtro de presentación. El portafolio es de la persona, no de la app.
3. **Escrituras**: el ministerio se deriva de la entidad (`program.ministry_id`, `honor.ministry_id`); el cliente nunca lo envía.
4. **Aplicación de origen**: el proxy `/api/v1/*` de cada frontend añade `X-Application: <slug>` (constante del servidor
   Next, no del navegador). El API la valida contra `applications` y la usa sólo para `certificates.application_id` y
   para que los enlaces de los correos (verificación, contraseña) apunten al `applications.domain` correcto en vez de a
   `FRONTEND_URL`. **Nunca para autorizar.**
5. **Sesión**: mismas credenciales en todas las apps; cada dominio tiene su cookie HttpOnly a través de su proxy. No hay
   inicio de sesión único entre dominios (fuera de alcance).
6. **CORS**: añadir los orígenes nuevos en el API y en el bucket privado de evidencias (`PUT, GET, HEAD`).

### 2.3 Gobierno de los programas de GM — migración `014_program_governance.sql`

`programs` gana `issuer_level varchar(12) NOT NULL DEFAULT 'CLUB'` (D3), `requires_verified boolean DEFAULT false`,
`requires_child_protection boolean DEFAULT false`, `min_age smallint NULL`; más la semilla de `applications.guiasmayores`.
`prerequisites_status(user, program)` devuelve `[{code: VERIFIED | CHILD_PROTECTION | MIN_AGE, met}]` a partir de campos
que **ya existen** en `users` (`verification_status`, `child_protection_completed`, `birth_date`; sin fecha de nacimiento
⇒ no cumplido con el mensaje «Completa tu fecha de nacimiento»). Se muestra en la carpeta y se exige al emitir (regla 10).
Los cuatro valores se declaran en el JSON de estructura del programa (sección 7) y los escribe el importador.
Emisor del certificado: la Asociación del club del miembro (hoy siempre NTAM).

Menores: tutelas y consentimientos son del CORE, así que un consentimiento `GRANTED` vale en todas las apps y el panel del
tutor muestra el portafolio completo del menor, sea cual sea el ministerio. Ninguna app nueva relaja una regla de A.

Despliegue de F4 (pasos del responsable): dominio y proyecto de Vercel con su «Root Directory»; origen nuevo en el CORS del
API (Render) y del bucket privado (Cloudflare); fila de `applications`; plantilla `investidura-guia-mayor` instalada. Orden:
`014` → backend → conversión a monorepo con `conquistadores.app` **sin cambios funcionales** y verificada en producción →
primera versión de `guiasmayores.app`.

### 2.4 Compartir el kit `cq-*` entre frontends

| Opción | A favor | En contra |
|---|---|---|
| Paquete npm versionado | Límite claro | Registro privado, build del paquete, versiones y **dos PR por cada cambio** de componente: demasiado para un equipo de una persona |
| `git subtree` / copia | Cero infraestructura | Deriva garantizada, fusiones dolorosas, ningún cambio atómico kit + pantalla |
| **Monorepo con workspaces** | Un cambio del kit y de las apps en un solo commit; Vercel admite un proyecto por «Root Directory» | Mover carpetas una vez |

**Recomendación: monorepo con npm workspaces, y sólo el día que se cree la primera pantalla de `guiasmayores.app`.** Ese día:
`apps/conquistadores`, `apps/guiasmayores`, `packages/cq-kit` (código fuente TypeScript, sin build propio, consumido con
`transpilePackages`). Al paquete van `components/cq`, `components/ui`, la capa `cq-*` de `globals.css`, `lib/api/*`, la
fábrica del proxy `/api/v1/*` (con `ALLOWED_PREFIXES` y `X-Application` por app) y los mensajes base de next-intl. Cada app
aporta sus rutas, su `config/navigation.ts`, sus variables de marca (`--cq-brand-*`), `public/brand` y un archivo de
mensajes que **sobrescribe terminología** («tarjeta» / «carpeta», «clase» / «programa»). Sin Turborepo hasta que el
build duela. Hasta entonces la única disciplina es no meter textos ni colores de Conquistadores dentro de `components/cq`.
Servir varios dominios desde una sola app Next (ministerio por `Host`) se descarta para GM —navegación y pantallas
distintas— y se reevaluará para Aventureros en F8, que comparte casi todas las pantallas de clases.

### 2.5 Pantallas de `guiasmayores.app` (F4)

Navegación de 5: **Inicio · Carpeta · Especialidades · Bitácora (F6) · Perfil**.
- **Inicio**: GM, EMC y CMJA con su avance, bloque **Prerrequisitos** (verificación, protección infantil, edad) con qué
  falta y a quién pedirlo, tarjeta del mentor (F5), próxima revisión trimestral (F7).
- **`/folder/[id]`**: la carpeta — secciones, requisitos con evidencias (componentes de A), requisitos enlazados a
  especialidades y a EMC. **`/honors`**: catálogo de Conquistadores reutilizado.
- **Panel de Asociación / Zona**: **Por revisar** (incluye directores aspirantes) y **Carpetas listas para investidura** →
  diálogo de emisión de A con plantillas `program` de `master-guides`.
- Pruebas propias de F4: `can_review` y `can_issue` por `issuer_level` (director de club **no** emite GM; Asociación de otra
  jurisdicción tampoco; director aspirante dictaminado por Zona); prerrequisitos bloquean emitir y no avanzar; correo de
  verificación con el dominio de la app de origen; `X-Application` desconocida se ignora; build de ambas apps desde el monorepo.

## 3. Mentoría · F5 — migración `015_mentoring.sql`

### 3.1 Modelo

`programs` gana `mentoring varchar(10) NOT NULL DEFAULT 'NONE'` (`NONE` \| `OPTIONAL` \| `REQUIRED`). Clases: `NONE`. GM:
`REQUIRED` (el Manual MG exige mentoría); EMC y CMJA: `OPTIONAL`. `REQUIRED` significa: **no se emite sin mentoría `ACTIVE`**.

**`mentor_profiles`**: `user_id` PK FK users, `status` (`PENDING` → `APPROVED`; `SUSPENDED`), `invested_on` date NULL,
`invested_place` varchar(180) NULL, `bio` varchar(600), `accepts_mentees` boolean DEFAULT true, `max_mentees` smallint
DEFAULT 5, `approved_by_id`, `approved_at`, `created_at`, `updated_at`.

**`mentorships`**: `id`, `enrollment_id` FK honor_enrollments, `mentee_id`, `mentor_id` FK users, `status`, `requested_by_id`,
`requested_at`, `mentor_responded_at`, `guardian_id` NULL, `guardian_approved_at` NULL, `director_approved_by_id` NULL,
`director_approved_at` NULL, `started_at`, `ended_at`, `ended_by_id`, `end_reason` varchar(500).
Estados: `REQUESTED` → (`PENDING_APPROVAL` si el aconsejado es menor) → `ACTIVE` → `ENDED`; `DECLINED`, `CANCELLED`.
Índice único parcial: una sola fila viva (`REQUESTED`, `PENDING_APPROVAL`, `ACTIVE`) por `enrollment_id`. Una solicitud sin
respuesta a los 30 días se trata como caducada **al leerla** (sin tarea programada).

### 3.2 Quién, con qué consentimiento y con qué alcance

- **Elegible** (D5), comprobado al aprobar el perfil **y en cada consulta de permiso**: adulto, cuenta `ACTIVE`,
  `VERIFIED`, `child_protection_completed`, perfil `APPROVED`, e investido GM (certificado del programa GM en la
  plataforma, o `invested_on` atestado por quien aprueba). Aprueba `can_approve_mentor`: `COORDINATOR_ZONE` /
  `ADMIN_ASSOCIATION` con jurisdicción sobre la organización del mentor, o `MASTER_GC`.
- **Consentimiento**: lo pide el aspirante (o lo propone su director); el mentor acepta o declina; si lo propuso el
  director, el aspirante también acepta. Menor: además tutor con consentimiento `GRANTED` **y** director de su club (D6).
- **Alcance**: una mentoría es de **una inscripción**. El mentor ve esa carpeta, sus evidencias (URL firmada) y las
  entradas de bitácora que el aconsejado comparta (F6). No ve otras inscripciones, certificados, actividades ni perfil.
- **Puede**: dictaminar requisitos de esa carpeta (D4) — entra en su cola **Por revisar**—, registrar la revisión
  trimestral de su aconsejado (F7). **No puede**: emitir, subir o borrar evidencias, aprobar horas, ver la bitácora no compartida.
- **Incompatibilidades** (422 al crear): mentor = aconsejado; mentor es tutor del aconsejado (no se dictamina a un hijo);
  mentor con `max_mentees` activos; mentor fuera de la Asociación del aconsejado.
- **Cambio de mentor**: cualquiera de los dos, el director del aconsejado o la Asociación la terminan con motivo; se crea
  otra. El historial se conserva y los dictámenes ya dados mantienen su `reviewed_by_id`. El acceso del mentor saliente
  termina en el acto: `is_active_mentor` mira `status = 'ACTIVE'` **y** la elegibilidad en vivo, de modo que suspender al
  mentor, retirarle la verificación o la marca de protección infantil «pausa» la mentoría sin tocar filas.

### 3.3 Salvaguarda cuando el aconsejado es menor

1. Doble aprobación (tutor + director) antes de `ACTIVE`; cualquiera de los dos puede terminarla después sin dar motivo al mentor.
2. **Ningún canal privado**: el bloque no construye chat, mensajes ni notas de sesión. Lo único que el mentor escribe son
   observaciones de dictamen y la revisión trimestral, y ambas las leen el menor, su tutor y los revisores de su club.
3. El API nunca entrega al mentor correo, teléfono, fecha de nacimiento ni dirección del menor: el esquema `MenteeOut` es
   nombre, club, programa y avance. El directorio de mentores tampoco expone contacto del mentor: se coordinan por el club.
4. Las observaciones no se pierden: F5 exige que `REQUIREMENT_REVIEW` guarde dictamen y observación en `metadata_json`,
   de modo que un dictamen nuevo sustituye al anterior en la fila pero no borra su rastro.
5. Toda lectura de evidencia pasa por `GET /evidences/{id}/url`; F5 añade la auditoría `EVIDENCE_VIEW` cuando quien pide la
   URL firmada no es el dueño (A no la registra), para saber qué adulto abrió qué foto de un menor.

### 3.4 API — `app/routers/mentoring.py`, prefijo `/api/v1/mentoring`

| Método y ruta | Quién | Qué |
|---|---|---|
| `PUT /profile` `{bio, accepts_mentees, max_mentees, invested_on?, invested_place?}` | adulto verificado | Crea o actualiza su perfil (`PENDING` la primera vez) |
| `GET /profiles/pending` · `POST /profiles/{user_id}/decision` `{status, invested_on?}` | `can_approve_mentor` | Habilita o suspende |
| `GET /mentors?q=` | autenticado con inscripción en programa con mentoría | Mentores `APPROVED` que aceptan, **de la Asociación del solicitante**: nombre, club, bio, año de investidura |
| `POST /mentorships` `{enrollment_id, mentor_id}` | dueño de la inscripción · director de su club (propuesta) | 409 si ya hay una viva; 422 por incompatibilidad |
| `POST /mentorships/{id}/respond` `{accept}` | mentor (y aspirante, si fue propuesta) | ⇒ `ACTIVE`, `PENDING_APPROVAL` o `DECLINED` |
| `POST /mentorships/{id}/approve` `{as: guardian \| director, approve}` | tutor `GRANTED` · director del club del menor | Con ambas ⇒ `ACTIVE` |
| `POST /mentorships/{id}/end` `{reason}` | cualquiera de las partes, tutor, director, Asociación | ⇒ `ENDED` |
| `GET /mentorships?role=mentor\|mentee` | yo | Mis mentorías con el avance de cada carpeta |

Auditoría: `MENTOR_PROFILE_SUBMIT`, `MENTOR_PROFILE_DECIDE`, `MENTORSHIP_REQUEST`, `MENTORSHIP_RESPOND`,
`MENTORSHIP_APPROVE`, `MENTORSHIP_END`, `EVIDENCE_VIEW`. Frontend (`guiasmayores.app`): **Buscar mentor**, tarjeta del mentor en la carpeta,
**Mis aconsejados** con su cola, **Quiero ser mentor**, aprobaciones en el panel del tutor, del director y de Zona/Asociación.
Kit: `MentorCard`, `MentorshipStatus`, `ApprovalSteps`.
Pruebas: matriz de elegibilidad (menor, sin verificar, sin protección infantil, sin investidura, suspendido); flujo adulto y
flujo menor con cada aprobación faltante; una sola mentoría viva; mentor revisa su carpeta y **no** otra inscripción del
mismo aconsejado ni su portafolio; mentor-tutor rechazado; acceso cortado al terminar y al suspender el perfil; `REQUIRED`
bloquea emitir; `MenteeOut` sin datos de contacto (test de esquema).

## 4. Bitácora espiritual · F6 — migración `016_journal.sql`

**`journal_entries`**: `id`, `user_id` FK ON DELETE CASCADE, `entry_date` date, `payload_enc` text NOT NULL (JSON
`{title, passage, body}` cifrado), `shared_with_mentor` boolean NOT NULL DEFAULT false, `created_at`, `updated_at`. Índice
`(user_id, entry_date DESC)`. Sólo texto (`body` ≤ 10 000 caracteres), sin adjuntos.
**`journal_shares`**: `id`, `user_id`, `progress_id` FK requirement_progress ON DELETE CASCADE, `date_from`, `date_to`,
`created_at`, `revoked_at` NULL — la **constancia**: autoriza a los revisores de ese requisito a ver conteos de ese rango.

- **Cifrado en la aplicación** (Fernet con rotación `MultiFernet`; variable `JOURNAL_ENCRYPTION_KEYS`; sin ella los endpoints
  responden 503 «Bitácora no configurada», como el bucket privado). Es dato de categoría especial (creencias): ni un volcado,
  ni la consola de Neon, ni un MASTER lo leen. Coste asumido: no hay búsqueda en el servidor y **perder la clave es perder
  las bitácoras** (la clave se guarda fuera de Render, en el gestor de secretos del responsable).
- **Privada por defecto** (D7). El autor puede marcar entradas sueltas como compartidas: las lee, sin poder escribir, su
  mentor con mentoría `ACTIVE`; si el autor es menor, también su tutor (aviso antes de compartir). Al terminar la mentoría
  el acceso cesa. Director, Zona, Asociación y MASTER **no tienen endpoint** de lectura.
- **Qué ven los revisores**: nada, salvo que el autor adjunte una constancia a un requisito («llevar un diario devocional
  tres meses»). Entonces el detalle del requisito muestra «42 entradas en 38 días distintos entre el 1 jul y el 30 sep».
  Nunca títulos, pasajes ni texto. El autor la revoca cuando quiera mientras el requisito no esté `COMPLETE`.
- **Edad**: disponible si la cuenta no es de menor o tiene 16 años cumplidos; para el resto, 403 con mensaje claro.
- **Retención**: vive hasta que su autor la borra; borrar es **definitivo e inmediato** (sin papelera). Borrar la cuenta
  borra la bitácora en cascada. No hay caducidad por inactividad. `GET /journal/export` entrega todo en JSON y Markdown.
  Los respaldos de Neon sólo contienen texto cifrado.

API — `app/routers/journal.py`, prefijo `/api/v1/journal` (todo «yo», salvo la lectura compartida)
| Método y ruta | Qué |
|---|---|
| `POST /entries` · `PUT /entries/{id}` · `DELETE /entries/{id}` | Alta, edición y borrado definitivo (sólo el autor) |
| `GET /entries?from=&to=&limit=&offset=` · `GET /entries/{id}` | Mis entradas descifradas |
| `PUT /entries/{id}/share` `{shared}` | Compartir o dejar de compartir con el mentor |
| `GET /shared?mentorship_id=` | Mentor activo (o tutor del menor): entradas compartidas de ese aconsejado, sólo lectura |
| `POST /shares` `{enrollment_id, position, date_from, date_to}` · `DELETE /shares/{id}` | Adjuntar o revocar constancia |
| `GET /summary?from=&to=` · `GET /export` | Mis conteos · exportación |

Auditoría **sin contenido** (acción + id): `JOURNAL_ENTRY_CREATE`, `_UPDATE`, `_DELETE`, `JOURNAL_SHARE`, `JOURNAL_UNSHARE`,
`JOURNAL_SHARED_READ` (cada lectura del mentor o tutor), `JOURNAL_SUMMARY_GRANT`, `JOURNAL_EXPORT`. Los textos nunca van a
Sentry ni a logs (filtro de cuerpo en esas rutas). Frontend: **Bitácora** (lista por fecha, editor a pantalla completa con
autoguardado de borrador local, conmutador «Compartir con mi mentor» con aviso, racha discreta), **Adjuntar constancia**
desde el requisito. Kit: `JournalEntryCard`, `JournalEditor`, `ShareToggle`.
Pruebas: la columna nunca contiene texto plano; rotación de claves; sin clave ⇒ 503; otro usuario, director, Asociación y
MASTER ⇒ 404; mentor lee sólo compartidas y sólo con mentoría activa; tutor lee compartidas de menor y nada de un adulto;
constancia = sólo números y se revoca; menor de 16 ⇒ 403; borrado irreversible; exportación completa; auditoría sin contenido.
Despliegue: generar y custodiar la clave, `JOURNAL_ENCRYPTION_KEYS` en Render, `016`, backend, frontend.

## 5. Revisión trimestral · F7 — migración `017_portfolio_reviews.sql`

Propósito (visión): **ánimo y acompañamiento**, con check de «carpeta / cursos / tarjeta». Es un registro; **no cambia el
estado de ningún requisito** ni sustituye al dictamen.

**`portfolio_reviews`**: `id`, `user_id` (miembro), `club_id`, `period` char(7) (`2027-Q1`, trimestres naturales),
`status` (`SCHEDULED` → `DONE`; `CANCELLED`), `scheduled_for` date NULL, `requested_by_id` NULL, `reviewer_id` NULL,
`held_on` date NULL, `folder_check`, `courses_check`, `card_check` varchar(14) (`ON_TRACK` \| `NEEDS_SUPPORT` \| `NA`),
`note` text (≤ 2000, **visible para el miembro y su tutor**), `agreements` text (≤ 1000, próximos pasos), `snapshot` jsonb
(contadores de cada inscripción activa y nº de certificados en ese momento), `created_at`, `updated_at`.
Único parcial `(user_id, period) WHERE status <> 'CANCELLED'`. «No realizada» no es un estado: es una `SCHEDULED` cuyo
trimestre terminó, calculado al leer.

Flujo: el director abre **Revisión trimestral** del trimestre → lista de miembros con inscripciones activas → **Programar**
(fecha, en bloque) → el día de la revisión abre a cada miembro, ve la foto automática del avance y registra los tres checks,
la nota y los acuerdos → `DONE`. El miembro y su tutor la ven en su portafolio. Zona puede **solicitar** la revisión de un
miembro (la visión: «solicita revisión de carpeta»): crea una `SCHEDULED` con `requested_by_id` que aparece al director.
Recordatorios: aviso dentro de la app («Faltan 12 revisiones de este trimestre»); por correo, cuando exista la
infraestructura de notificaciones del bloque E.

Permisos: registra `can_record_review` = jurisdicción de club de `can_review`, el mentor activo (sólo de su aconsejado) y,
para directores, Zona / Asociación; nunca uno mismo. Lee `can_view_portfolio`. Sin notas ocultas: todo lo que un adulto
escribe sobre un menor lo lee su tutor.

| Método y ruta (`/api/v1/portfolio/reviews`) | Quién | Qué |
|---|---|---|
| `GET ?period=&club_id=` | `can_record_review` | Miembros del club con su estado en el trimestre |
| `POST /schedule` `{period, user_ids[], scheduled_for?}` | `can_record_review` · Zona (solicitud) | Idempotente por miembro y trimestre |
| `PUT /{id}` `{held_on, folder_check, courses_check, card_check, note, agreements}` | `can_record_review` | Toma la foto `snapshot` y pasa a `DONE`; editable por quien la registró durante 7 días |
| `DELETE /{id}` | quien la programó | `CANCELLED`, sólo `SCHEDULED` |
| `GET /users/{user_id}` · `GET /me` | `can_view_portfolio` · yo | Historial |
| `GET /summary?period=` | Zona / Asociación | Por club: miembros con inscripción activa, revisados y pendientes (sin notas) |

Auditoría: `PORTFOLIO_REVIEW_SCHEDULE`, `_RECORD`, `_UPDATE`, `_CANCEL`. Frontend (en cada app que exista en ese momento;
tras F4 la pantalla vive en `cq-kit`): pantalla del director con la lista del
trimestre y la hoja de registro; sección «Revisiones» en `/portfolio`; resumen por club para Zona y Asociación. Kit:
`ReviewPeriodList`, `ReviewSheet`, `CheckTriad`. Pruebas: unicidad por trimestre; foto correcta; «no realizada» calculada;
director no se revisa; mentor sólo a su aconsejado; tutor la lee; Zona ve el resumen sin notas; ventana de edición de 7 días.

## 6. Aventureros · F8 — migración `018_managed_profiles.sql` (depende del bloque E)

- **Perfil gestionado** (D8): `users.is_managed boolean NOT NULL DEFAULT false`. Lo crea **sólo un tutor** con cuenta
  (`POST /users/children` `{name, birth_date}`): nace con la tutela `GRANTED` (el consentimiento es el acto de crearlo;
  quedan fecha, IP y texto aceptado en auditoría), correo sintético `<uuid>@managed.invalid` y contraseña inutilizable.
  `login`, `refresh`, recuperación de contraseña y cualquier envío de correo **rechazan o ignoran** cuentas gestionadas.
  Un director no crea niños: eso dejaría datos de un menor sin consentimiento parental (KPI de la visión: 100 %).
- El tutor solicita el ingreso del niño al club con el flujo de membresía del bloque E. El club lo inscribe en su clase y
  firma la tarjeta con las herramientas de F3. **El tutor sólo lee** (regla de la visión); no hay «actuar en nombre de».
- Al pasar a Conquistadores: `POST /users/children/{id}/claim` `{email}` → correo de verificación → `is_managed = false`;
  conserva su portafolio completo.
- Minimización: nombre, fecha de nacimiento y club; sin avatar con foto; las clases de Aventureros se cargan con
  `evidence_required = false`, y como sólo el dueño sube evidencias y el dueño no inicia sesión, **no se almacenan fotos
  de niños de 4–9 años**. Fuera del club y de su tutor, el nombre se muestra como nombre + inicial.
- Contenido: clases (Abejitas laboriosas, Rayitos de sol, Constructores, Manos ayudadoras y las de preescolar) como
  `programs` de `adventurers`; especialidades de Aventureros como `honors` de ese ministerio; plantilla
  `investidura-aventureros`. Frontend: tercera app del monorepo, o la misma app de clases servida por `Host` si para entonces
  las pantallas resultan ser las mismas (se decide ahí, con el código delante). Panel del tutor: **Mis hijos** → alta, club, tarjeta.
- Auditoría: `MANAGED_PROFILE_CREATE`, `MANAGED_PROFILE_CLAIM`. Pruebas: una cuenta gestionada no puede autenticarse por
  ninguna ruta ni recibe correos; sólo un tutor la crea; firma en bloque sobre gestionados; el tutor no escribe progreso;
  `claim` exige verificar el correo; nombre abreviado fuera de jurisdicción.

## 7. Fuentes de contenido e idiomas

- **Fuente 1 — Pathfinder Wiki** (`wiki.pathfindersonline.org`), la misma de las especialidades, que también aloja las
  clases y el currículo de Guía Mayor. **Mismas reglas de `ESPECIALIDADES_WIKI.md`**: texto CC BY-SA 3.0 con `source`,
  `source_url` y `license` por fila y «Fuente: Pathfinder Wiki, CC BY-SA» con enlace donde se muestre; sólo páginas
  normales (nunca `/w/api.php`, `/w/index.php?` ni `/w/Special:`), **una petición cada 10 s**, User-Agent identificable,
  HTML crudo fuera del repo (`~/adventist-wiki`), rastreo reanudable. Las imágenes de insignias de la wiki no se copian.
- Esas páginas **no tienen la forma regular** de `AY_Honors/<título>/Requirements`. Primer paso, a mano: un inventario
  `migrations/data/wiki_program_pages.csv` (`program_slug, locale, url`) revisado por una persona; el rastreador sólo baja
  lo que está en ese archivo. Son decenas de páginas, no cientos.
- Herramientas (`migrations/catalog_tools/`): `crawl_wiki_programs.py`, `parse_wiki_program.py` (encabezados → secciones,
  listas numeradas → requisitos de primer nivel; los incisos quedan dentro del texto) y `migrations/import_programs.py`
  (idempotente, simulacro por defecto, `--commit`). Formato intermedio versionado en el repo:
  `migrations/data/programs/<ministry>/<slug>.json` (estructura) y `<slug>.<locale>.json` (textos).
- **El analizador no adivina tipos.** `kind`, `target_honor_slug`, `target_program_slug`, `target_quantity` y
  `evidence_required` los anota una persona en el JSON de estructura; el importador resuelve slugs y **falla en voz alta** si
  una especialidad no existe en el catálogo.
- Lo importado entra como `DRAFT`. Se publica tras cotejarlo con el manual vigente en el territorio (D2) y se marca
  `authority`. El importador **nunca modifica un programa publicado**: si el contenido cambió, crea la versión siguiente en borrador.
- **Fuente 2 — manuales de la División** (p. ej. DIA para NTAM) cuando difieran de la wiki: mismo JSON, `source = 'manual'`,
  cargado a mano. No son CC BY-SA: el responsable debe contar con autorización de uso antes de publicarlos.
- **Idiomas**: la estructura es única; un idioma se importa sólo si su número de requisitos por sección coincide con la
  estructura (si no, se omite y se informa, como las 19 traducciones parciales de especialidades). Resolución: idioma pedido →
  español → el que exista; siempre explícito (`?locale=`), nunca `Accept-Language`. La inscripción fija el idioma.

## 8. Incrementos, en orden de dependencia

| # | Incremento | Migración | Entrega | Depende de |
|---|---|---|---|---|
| F1 | Programas y tarjeta | `012_programs.sql` | Catálogo de programas, inscripción en programa, requisitos `FREE` por secciones, certificado de investidura emitido por el director (`meta.json` de plantillas + plantilla `investidura-clase`), importador, clase *Amigo* publicada en `conquistadores.app` | A en producción |
| F2 | Requisitos enlazados y horas | `013_activity_logs.sql` | `HONOR` (concreto y abierto), `PROGRAM`, `HOURS`; registro y aprobación de actividades; resto de clases de Conquistadores | F1 |
| F3 | Trabajo del club sobre la tarjeta | — | Inscripción por el club, matriz y firma en bloque | F1 |
| F4 | `guiasmayores.app` y gobierno de GM | `014_program_governance.sql` | Monorepo + `cq-kit`, `X-Application`, correos por dominio, plantilla `investidura-guia-mayor`, emisión y revisión por Asociación, prerrequisitos, currículos GM / EMC / CMJA | F1, F2 |
| F5 | Mentoría | `015_mentoring.sql` | Perfiles de mentor, mentorías con consentimientos, mentor en `can_review`, `REQUIRED` para emitir GM | F4 |
| F6 | Bitácora | `016_journal.sql` | Diario cifrado, compartir por entrada, constancia por conteos, exportación | F4 (compartir: F5) |
| F7 | Revisión trimestral | `017_portfolio_reviews.sql` | Programar, registrar, historial, resumen para Zona / Asociación | F1 (mentor: F5) |
| F8 | Aventureros | `018_managed_profiles.sql` | Perfiles gestionados, clases y especialidades de Aventureros, tercera superficie | F3, **bloque E** |
| F9 | Requisito «curso / examen» | `019_course_requirements.sql` | `kind = COURSE` enlazado a lo que definan los bloques B–D, vía `auto_complete`; el curso de protección infantil de la plataforma marca `child_protection_completed` | F2, **bloques B–D** |

Cada incremento es su propio ciclo spec breve → plan → implementación con TDD, y se puede detener tras cualquiera sin dejar
nada a medias. F3 y F7 sólo dependen de F1 y pueden adelantarse si el piloto lo pide. La numeración `012+` presupone que
`008`–`011` (membresía, cursos, exámenes) ya se usaron; si F1 llega antes, se toma el siguiente número libre.

## 9. Lo que NO se construye antes de tiempo

- Ninguna tabla de F antes de que A esté en producción con un club real completando el flujo.
- Monorepo, paquete `cq-kit` o cualquier extracción del kit antes de la primera pantalla de `guiasmayores.app`.
- `kind = COURSE`, su columna y su FK antes de que existan las tablas de B–D.
- Columnas de gobierno (`issuer_level`, prerrequisitos, `mentoring`) antes de F4 / F5: una columna sin comportamiento se pudre.
- Variantes territoriales de un programa (`org_scope_id`) y publicación por Asociaciones: sólo si D2 lo acaba exigiendo.
- Editor de programas en pantalla (es de `admin.adventist.club`): en F entran por JSON + importador.
- Validación en dos pasos, firma por sección o estados nuevos de requisito (D4 b, D3 c).
- Motor genérico de reglas para requisitos: son cuatro tipos (`FREE`, `HONOR`, `PROGRAM`, `HOURS`) escritos a mano, y un quinto en F9.
- Tareas programadas: caducidades y «no realizada» se calculan al leer.
- Notificaciones por correo de F: esperan a la infraestructura del bloque E; mientras, avisos dentro de la app.
- Renombrar `honor_enrollments` o cualquier tabla de A.

## 10. Fuera de alcance de F

Chat o mensajería de cualquier tipo; notas privadas de sesión de mentoría; bitácora para menores de 16, con adjuntos o con
búsqueda en el servidor; inicio de sesión único entre dominios; creación de niños por el director; «actuar en nombre de»;
vídeo y audio como evidencia (siguen la agenda de A); insignias y pines físicos en Sevenpxs; puntajes de clubes, Club de
Honor e informes ANT (otro bloque de «operación del club»); firma criptográfica y anulación de certificados (pendientes
del CORE; F sólo deja escrito el contrato de la regla 11); estadísticas por Asociación más allá del resumen de revisiones.

## 11. Riesgos y supuestos

- **Contenido** es el camino crítico, no el código: cotejar cada clase con el manual vigente lleva más tiempo que F1.
- La wiki refleja el currículo de la DNA / AG; los clubes de NTAM trabajan con el de la DIA. D2 (c) lo absorbe, pero puede
  significar cargar a mano buena parte del texto en español y conseguir autorización.
- Una cuenta pertenece hoy a **una** organización (`users.organization_id`). Si un aspirante a GM sirve en un club de
  Conquistadores y además está en un club de GM, el bloque E debe decir cuál es «su club» para revisar; F no lo resuelve,
  sólo deja un único punto donde cambiarlo (1.5).
- El bloque E puede definir su propia forma de alta de menores sin correo: `018` debe reconciliarse con `008` antes de escribirse.
- La clave de cifrado de la bitácora es un secreto operativo nuevo con pérdida irreversible: custodia y respaldo son
  responsabilidad del responsable y se documentan en `ESTADO.md` al desplegar F6.
