# Anexo de contexto — versión inicial de la visión y MVP por fases

> Texto aportado por el responsable del proyecto (21 sep 2026) como "más contexto". Es anterior a
> `VISION_ECOSISTEMA.md`; donde difieran, manda aquel. Se conserva porque detalla el MVP, los reportes ANT,
> el descubrimiento por ubicación y la monetización.
>
> ⚠️ **Superado:** la "infraestructura técnica sugerida" de este borrador (NestJS + GraphQL, Firebase/Auth0,
> Strapi/Directus, Shopify/WooCommerce) **no** es la arquitectura vigente. La real es Next.js (Vercel) →
> FastAPI (Render, `api.adventist.club`) → Neon PostgreSQL, con R2 para medios y auth propia (JWT + cookies
> HttpOnly). Ver `ESTADO.md`.

## Visión

Ecosistema digital centralizado para todo el Ministerio Joven Adventista (MJA): conecta los clubes físicos
con su identidad digital, unifica formación, mentoría y certificación, y promueve la interacción ministerial
global bajo control institucional. Refleja la estructura eclesiástica:
Asociación → Zona → Iglesia → Club → Unidad → Miembro.

| Dominio | Enfoque |
|---|---|
| adventist.club | CORE: usuarios, roles, API, certificados, auditoría, eventos, almacenamiento, geolocalización. A futuro, red social JA |
| conquistadores.app | Dashboard de Conquistadores: aprendizaje, evidencias, cursos, reportes ANT. Aquí los instructores crean y publican especialidades |
| guiasmayores.app | EMC / CMJA / MG: carpeta digital del Guía Mayor, entrenamiento de clubes, conexión con especialidades de Conquistadores |
| sevenpxs.com | Marketplace: parches solo con certificado verificado por adventist.club; además merch y archivos vectoriales / de bordado (estos sin conexión a la API) |

## Roles (versión inicial)

1. **Coordinador General (control maestro):** crea, asigna y modifica especialidades oficiales o locales;
   autoriza instructores y coordinadores de zona; administra eventos y camporís digitales; revisa
   estadísticas globales y por zona; gestiona biblioteca global y comunicados; valida la conexión de
   sevenpxs.com y las compras verificadas; emite o anula certificados globales; valida la base de datos
   local por ciudades; publica blogs e información relevante.
2. **Coordinador de Zona:** acceso maestro a los usuarios de una zona de la ciudad; revisa reportes y
   estadísticas de clubes; aprueba instructores con carta pastoral; valida cursos y contenidos antes de
   publicarse; supervisa informes trimestrales.
3. **Director de Club:** administra el club digital; registra miembros y asigna unidades; aprueba cursos,
   evidencias (incluidas las manuales) y horas de servicio, que él mismo asigna; emite certificados; genera
   reportes ANT y checklist del Club de Honor; aprueba compras duplicadas de parches.
4. **Instructor / Guía Mayor:** crea cursos, exámenes y materiales (solo en conquistadores.app); revisa
   tareas; sube recursos (PDF, video…); valida evidencias y aprueba certificados; publica tras revisión
   zonal. Requisito: carta firmada por su iglesia local y validada por el Coordinador de Zona → estado
   "Instructor activo verificado". **Instructores, guías mayores y directores deben subir una carta aprobada
   por la iglesia.**
5. **Conquistador / Guía Mayor estudiante:** cursos, evidencias, exámenes, certificados con QR, portafolio
   JA. Los Guías Mayores además: flujos EMC, CMJA y MG, carpeta de evidencias y bitácora devocional.
6. **Padres / Tutores:** aprueban la participación de menores y consultan progreso, certificados y
   actividades.

Cada **club digital tiene un ID** y se liga al club físico real: ciudad, estado y país, y al crearlo se
indica a qué **zona e iglesia** pertenece (de ahí sale el alcance del coordinador de zona).

## Flujo de especialidades

Borrador del instructor → revisión del Coordinador de Zona → aprobación del Coordinador General →
publicada (visible para clubes) → el Conquistador/GM la toma (desde conquistadores.app o guiasmayores.app)
→ examen ≥ 80 % ⇒ certificado automático; si requiere práctica ⇒ revisión manual por instructor o director
→ certificado con QR (almacenado en adventist.club y enviado por correo) → habilita la compra del parche
en sevenpxs.com.

Cada especialidad incluye: nombre, código, categoría, división, idioma; objetivos oficiales (CG);
materiales multimedia; actividades y exámenes; banco de preguntas; estado (borrador, revisión, aprobada,
pública); certificación con QR. Las especialidades base son las de la **Conferencia General**.

Regla de certificación: si la especialidad **no** tiene parte física basta aprobar el examen digital para
emitir el certificado automáticamente; si la tiene, lo aprueba el director o instructor tras revisar.

## Marketplace

Sincronizado con la API central; verifica el certificado antes de autorizar; bloquea duplicados: el usuario
no puede comprar más de un parche con el mismo certificado simultáneamente; solo el director (o instructor)
libera un duplicado. Un certificado = una compra válida del parche.

## MVP por fases

**Fase 1 — Núcleo del ministerio**
1. *Identidad y acceso:* Administrador de Asociación → Coordinador de Zona → Director de Club →
   Consejero/Instructor → Miembro (menor / adulto); correo/clave y opción federada (Google/Apple);
   consentimiento parental digital obligatorio para menores.
2. *Jerarquía:* País → Asociación/Misión → Zona → Club → Unidad; cada club con coordenadas (lat/lon),
   horarios, cupo y estatus (activo/suspendido).
3. *Descubrimiento por ubicación:* con permiso de ubicación la app muestra clubes cercanos (radio
   configurable, p. ej. 5–20 km); filtros por día de reunión, ministerio (Aventureros / Conquistadores / GM),
   cupo, accesibilidad.
4. *Portafolio / evidencias:* carpeta por persona con secciones (Liderazgo, Estilo de vida, Espiritual,
   Comunidad, "Share"), mentor asignado y revisiones (el Manual MG exige mentoría y revisión de portafolio).
   Evidencias: fotos, PDF, audio/video, enlaces; cada ítem con fecha, lugar, verificador y dictamen
   (Completo / Incompleto + observaciones).
5. *Especialidades como cursos:* objetivos, requisitos, materiales, lecciones, tareas con evidencia y examen
   (banco de reactivos por división/idioma). Modo "Formulatio": formularios guiados que recogen respuestas +
   evidencias (fotos de actividades físicas, cuadernos, salidas). ANT pide fotos en informes: espacio
   dedicado y **marca de verificación anti "foto posada"**.
6. *Exámenes:* opción múltiple, V/F, respuesta corta, carga de evidencia; intentos, temporizador, banco
   aleatorio, umbral, retroalimentación; "proctor ligero" (código de sesión del instructor en presenciales).
7. *Certificados:* PDF con QR verificable (hash + endpoint público); el portafolio consolida certificados y
   progreso.

**Fase 2 — Operación del club y reportes ANT**
1. Informes mensuales/trimestrales: plantillas con 5+ fotos por mes y resumen; la app genera el informe
   listo para enviar.
2. Club de Honor: puntos y fechas clave (registro, plan anual, recolección…), reglas del checklist (seguro,
   vigencias, evidencias).
3. Revisión trimestral: agenda y check de "carpeta / cursos / tarjeta" con recordatorios y dictamen, con
   propósito de ánimo y acompañamiento.

**Fase 3 — Gestión académica y contenidos**
1. Repositorio de recursos por especialidad: guías, plantillas, rúbricas, videos, lectura devocional,
   enlaces por división/idioma.
2. CMT/EMC y MG: flujos con prerrequisitos (verificación, curso de protección infantil), mentor, bitácora
   devocional y evidencias, evaluados "Completo / Incompleto" según el Manual MG.

**Fase 4 — Marketplace y monetización** (sevenpxs.com). **Fase 5 — Comunidad global** (adventist.club como
red social JA).

## Monetización

- *Institucional (B2B):* licencia anual por Asociación o Unión según clubes activos; módulos premium (Club
  de Honor PRO, analíticas ejecutivas); capacitaciones y soporte oficial.
- *Usuarios (B2C):* parches, merch y kits digitales; comisión simbólica para instructores creativos;
  subvención solidaria para clubes de bajos recursos.
- *Donaciones:* JA Solidario; % de ingresos a becas o seguros ministeriales.
- *Eventos:* inscripciones a camporís y congresos con pagos integrados; certificados y medallas digitales
  por participación.

## KPIs (versión inicial)

| Indicador | Meta | Responsable |
|---|---|---|
| Finalización de cursos | ≥ 90 % (aprobados por director o instructor) | Director / Instructor |
| Retención anual | ≥ 80 % (el club digital va de la mano del físico) | Director / Coordinador |
| Certificados emitidos | 100 % digitales con QR, enviados por email y ligados al usuario | Sistema |
| Clubes activos | 100 % con reportes ANT (revisiones del coordinador, estadísticas de avance de los niños) | Coordinador |
| Compras verificadas | 0 duplicados | Sevenpxs / Core |
| Horas de servicio | 100 % registradas y aprobadas | Director |
| Seguridad infantil | 100 % consentimiento parental | Sistema / Padres |

## Gobernanza y seguridad

Autorización parental obligatoria; filtros de contenido y moderación; privacidad y protección infantil
(GDPR/DIA); bitácora de auditoría; RBAC granular; versionado de cursos y actividades; plan de respaldo,
restauración y continuidad.

## Beneficios institucionales

Unificación de procesos JA; plataforma segura, verificable y auditable; menos papeleo y errores manuales;
ecosistema autosostenible; alineación con los manuales oficiales de la Conferencia General; base para
expandirse a otras Uniones y Divisiones.
