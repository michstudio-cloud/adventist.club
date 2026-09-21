# ADVENTIST.CLUB — Ecosistema Digital Global del Ministerio Joven Adventista

> "Una sola misión, una comunidad conectada."
>
> Documento de visión y manual operativo aportado por el responsable del proyecto (21 sep 2026).
> Es **contexto de producto**: define hacia dónde va la plataforma. El estado real de lo construido
> está en `docs/ESTADO.md`; las decisiones técnicas de idiomas y plantillas en `docs/I18N_Y_PLANTILLAS.md`.

## 1. Visión general

Adventist.Club es el ecosistema digital oficial diseñado para centralizar la gestión, formación,
certificación y conexión global del Ministerio Joven Adventista. Su propósito:

- Conectar clubes físicos con su identidad digital.
- Unificar procesos de formación (Especialidades, EMC, CMJA, MG).
- Garantizar trazabilidad ministerial.
- Emitir certificados digitales verificables.
- Integrar un marketplace oficial validado por certificados.
- Promover mentoría, liderazgo y comunidad global.

El sistema respeta y refleja la estructura eclesiástica oficial.

## 2. Estructura jerárquica global

División → Unión → Asociación / Misión → Zona / Distrito → Iglesia local → Club → Unidad → Miembro.

Ningún club digital puede existir sin iglesia asignada, zona asignada, asociación asignada, y unión y
división definidas.

## 3. Dominios y plataformas

**adventist.club (CORE)** — plataforma central, "el cerebro del sistema": gestión global de usuarios,
base de datos jerárquica multitenant, API central, autenticación y roles (RBAC), certificados digitales
con QR y hash, auditoría global, biblioteca institucional, estadísticas mundiales, eventos oficiales,
conexión con Sevenpxs.com.

**conquistadores.app** — dashboard para Conquistadores: especialidades digitales, cursos y exámenes,
evidencias, reportes ANT, gestión de unidades, certificados. Conectado 100 % al CORE.

**guiasmayores.app** — formación avanzada: EMC (Entrenamiento en Ministerio de Clubes), CMJA
(Certificación de Ministerio Juvenil Adventista), programa de Guía Mayor (recursos y pasos para completar
la tarjeta, en este caso digital), carpeta digital de evidencias ligada a cada requisito de la carpeta de
Guía Mayor, mentoría (conectar con otro Guía Mayor mentor), bitácora espiritual (diario digital),
evaluaciones por sección (el coordinador evalúa el proceso). Conectado 100 % al CORE.

**sevenpxs.com** — marketplace oficial: venta de parches físicos verificados, merchandising JA (stickers,
gorras, sombreros), vectores y bordados digitales, validación por certificado digital (solo para comprar
parches de especialidades físicos, limitado a uno cada 6 meses), bloqueo de compras duplicadas (más de dos
parches con el mismo certificado), control por API con adventist.club para enlazar productos desde
conquistadores.app o adventist.club.

**admin.adventist.club** — ver sección 9.

## 4. Roles

1. **MASTER GC (Global Ecosystem Administrator)** — alcance global. Crea y administra Divisiones, Uniones
   y Asociaciones; configura políticas globales; carga y versiona especialidades oficiales GC; emite
   certificados globales (camporís mundiales); audita certificados de Asociaciones; supervisa estadísticas
   mundiales; controla conexión y seguridad de Sevenpxs; administra infraestructura, backups y seguridad;
   activa bloqueos por fraude; define políticas de protección infantil.
   *Limitaciones:* no interviene en la gestión diaria de clubes, no aprueba evidencias individuales, no
   sustituye a la autoridad local. Opera bajo doble autenticación, log de auditoría inmutable y consejo
   digital colegiado (3–5 miembros).
2. **Administrador de División** — supervisa Uniones de su territorio, valida especialidades divisionales,
   supervisa eventos continentales, revisa estadísticas regionales y cumplimiento doctrinal.
3. **Administrador de Unión** — supervisa Asociaciones, valida estructura regional, coordina eventos
   nacionales, apoya auditoría ministerial.
4. **Administrador de Asociación / Coordinador General** — autoridad máxima de la Asociación: autoriza
   instructores, aprueba especialidades locales, supervisa zonas, valida informes, emite certificados
   oficiales, administra eventos locales, gestiona biblioteca regional, supervisa seguridad infantil,
   autoriza duplicados en marketplace.
5. **Coordinador de Zona** — supervisa clubes de su zona, revisa reportes trimestrales, valida
   instructores con carta pastoral, aprueba cursos antes de escalar, da acompañamiento pastoral.
6. **Director de Club** — registra miembros, asigna unidades, aprueba evidencias físicas y horas de
   servicio, emite certificados manuales, genera reportes ANT, supervisa carpeta digital, autoriza
   recompra de parches.
7. **Instructor / Guía Mayor** — requisitos: carta firmada por iglesia, validación zonal, estado activo
   verificado. Crea especialidades, diseña exámenes, sube contenido, revisa evidencias, aprueba
   certificados, publica especialidades tras aprobación.
8. **Conquistador / Guía Mayor estudiante** — accede a cursos, sube evidencias, realiza exámenes, obtiene
   certificados digitales, ve su portafolio JA, completa EMC y MG.
9. **Padre / Tutor** — aprueba participación, firma consentimiento digital, ve progreso, descarga
   certificados. No puede modificar evidencias.

## 5. Flujo de especialidades

Instructor crea especialidad → revisión zonal → aprobación de Asociación → publicación → usuario toma el
curso → examen ≥ 80 % → si requiere práctica, revisión manual → certificado digital con QR → habilita
compra en Sevenpxs.

Tipos: oficiales GC (solo lectura), divisionales, locales. Al publicar se asigna código único y se activa
el examen. No se puede modificar una especialidad publicada sin versionado; los cambios generan nueva
versión y los certificados conservan siempre la versión aplicada.

## 6. Exámenes

Opción múltiple, verdadero/falso, respuesta corta, evidencia multimedia. Banco de preguntas aleatorio,
umbral mínimo configurable (80 %), intentos limitados, temporizador opcional, código de sesión para
eventos presenciales.

## 7. Certificados digitales

Incluyen nombre completo, ID único, código de especialidad, fecha, instructor aprobador, director
aprobador, hash criptográfico y QR verificable público. El endpoint público muestra nombre, club, estado
válido/inválido y fecha.

Se emiten cuando el examen está aprobado, las evidencias aprobadas y el director valida si aplica. Quedan
en la base central con historial permanente. Si se detecta fraude: se anula, se marca inválido y se
notifica a la autoridad.

## 8. Portafolio digital JA

Secciones: Liderazgo, Estilo de vida, Crecimiento espiritual, Servicio comunitario, Share Your Faith.
Cada evidencia incluye fecha, lugar, archivo multimedia, mentor asignado, dictamen (Completo /
Incompleto) y observaciones. Estados: pendiente, en revisión, completo, incompleto. El mentor revisa, el
director valida, la Asociación audita si es necesario.

## 9. admin.adventist.club

- Acceso solo para personal autorizado.
- **Creación de especialidades multilenguaje:** imagen del parche; galería de recursos con notas o
  descripción por imagen o evidencia; PDFs; bloques tipo curso en línea para completar la especialidad.
  **Importante:** las especialidades con actividades físicas o manuales exigen subir evidencia, y la
  aprueban coordinador de distrito o de asociación, pero **en primer lugar el director del club físico**.
  Por eso cada club digital debe ligarse a un club físico o a una iglesia local. Los clubes pueden tener
  nombres distintos pero deben tener el mismo director. Quien agrega y conecta al director con el club
  físico es el **admin de la Asociación**.
- **Admin de Asociación:** control total solo sobre su Asociación — crear y administrar usuarios, proponer
  actividades, crear eventos con notificaciones solo para los usuarios ligados a la Asociación, y un
  apartado de **puntajes para clubes**.
- **Rol Secretaría** (otorgado por el admin): selecciona clubes y ve sus datos (miembros, directiva);
  asigna o reasigna cargos, lo que afecta directamente credenciales y accesos (si cambia el director y el
  nuevo no tiene cuenta, envía un enlace de registro por correo o WhatsApp; para WhatsApp solo genera el
  enlace con texto); previsualiza o modifica datos de control del club físico.
- **Puntajes (gamificación):** la secretaría crea categorías y tareas que el club debe cumplir y les asigna
  puntos; en el periodo que elija la Asociación los clubes completan los retos. El director del club (o un
  Guía Mayor designado por él) sube las evidencias desde una pestaña propia — evaluar si conviene una
  página de club tipo "Facebook" donde los designados publiquen y suban evidencias de los retos.
- **Contenido para Guías Mayores, EMC y CMJA:** módulos de aprendizaje con actividades y formas de
  concluirlas; como no se resta peso a lo físico, cada actividad puede completarse en digital o en físico
  subiendo la evidencia. Para los requisitos de Guía Mayor: crear cada módulo y sección con todas las
  opciones posibles.
- **Fase 4:** control y check-in de eventos físicos (QR de acceso) y control financiero eficiente (todo en
  efectivo).

## 10. Marketplace (Sevenpxs)

Usuario intenta comprar parche → el sistema consulta la API → verifica el certificado → si es válido y no
usado, autoriza → lo marca como redimido. Duplicados: bloqueo automático; solo el Director puede autorizar
un duplicado y queda registro de la autorización.

## 11. Principios de funcionamiento

1. Autoridad descendente, operación ascendente.
2. El club digital nunca reemplaza al club físico.
3. Toda certificación debe estar supervisada.
4. La protección infantil es obligatoria.
5. Cada acción queda registrada (auditoría).
6. Un certificado = una validación real.
7. La estructura eclesiástica no se altera digitalmente.

Cada usuario tiene un rol, una jurisdicción, un alcance territorial y un conjunto de permisos. Nadie puede
actuar fuera de su jurisdicción (un Director de Club solo aprueba evidencias de su club).

## 12. Operación por niveles

- **MASTER GC:** configura el ecosistema; acceso con doble autenticación; acciones críticas con doble
  aprobación; puede suspender cuentas institucionales por fraude; no interviene en operaciones locales.
  Ante irregularidades: genera alerta → notifica a la Asociación → activa auditoría → puede bloquear
  temporalmente la emisión de certificados.
- **Administrador de Asociación:** panel regional, estadísticas por club, reportes oficiales, puede
  suspender instructores, valida cartas pastorales y Club de Honor, no puede modificar especialidades GC.
  Auditoría automática, registro de aprobación, historial de cambios visible.
- **Coordinador de Zona:** ve todos los clubes de su zona, genera alertas, recomienda suspensión, solicita
  revisión de carpeta, valida instructores antes de la Asociación.
- **Director de Club:** panel exclusivo de su club, aprueba con firma digital, devuelve evidencias
  incompletas, no modifica datos jerárquicos; reporta irregularidades a zona, puede suspender
  temporalmente a un usuario y solicitar revisión regional.
- **Instructor:** crea especialidad → revisión zonal → Asociación aprueba → se publica → usuarios se
  inscriben → evalúa evidencias → aprueba o rechaza → el sistema emite el certificado. Historial de
  evaluación y notas; no puede emitir certificados fuera de su especialidad.
- **Conquistador / GM:** solo se inscribe si pertenece a un club; evidencias requieren aprobación; examen
  automático si no hay parte física; con parte práctica espera revisión manual; el certificado queda en
  su portafolio.
- **Padre / Tutor:** debe autorizar antes de activar la cuenta del menor; ve progreso; no edita
  información académica.

## 13. Control, auditoría y conflictos

Se registra quién creó, quién aprobó, fecha y hora, IP y cambios. Alertas automáticas por emisión masiva
inusual, reprobaciones anormales, duplicados frecuentes e instructores inactivos.

Conflictos: Director → Zona → Asociación; el Master GC solo interviene si es sistémico.

Suspensión: Director → miembro; Zona → director; Asociación → instructor; Master GC → institución
completa. Toda suspensión lleva motivo, queda registrada y tiene tiempo definido.

## 14. Protección infantil

Consentimiento digital obligatorio; verificación de instructores; curso de protección infantil para
instructores; filtro y moderación de contenido; restricción de mensajería privada adulto-menor; registro
de comunicación; control granular de acceso; auditoría permanente; política GDPR / protección de datos.

## 15. Eventos

Locales, regionales, nacionales, globales. Emiten certificados digitales, medallas digitales e insignias
especiales.

## 16. Monetización

Institucional: licencia anual por Asociación, soporte técnico, módulos premium. Usuarios: parches,
merchandising, vectores y bordados digitales. Solidario: donaciones JA, fondo de becas, apoyo a clubes de
bajos recursos.

## 17. KPIs

Finalización de cursos 75–85 % · retención anual 70–80 % · certificados digitales 100 % · consentimiento
parental 100 % · clubes activos reportando 95 % · compras duplicadas 0 %.

## 18. Roadmap

Fase 1: identidad y estructura · Fase 2: especialidades y certificados · Fase 3: EMC y MG ·
Fase 4: marketplace (y check-in / finanzas de eventos) · Fase 5: comunidad global.

Expansión futura: credencial JA global, ID ministerial permanente, estadísticas históricas de liderazgo,
integración con camporí mundial, comunidad social moderada.

## 19. Objetivo estratégico

Centralizar la formación ministerial, garantizar trazabilidad real, validar instructores oficialmente,
integrar un marketplace verificable, crear identidad JA digital global, y **fortalecer el club físico, no
reemplazarlo**. No es una red social independiente: es una infraestructura ministerial digital oficial —
un ERP ministerial, un LMS jerárquico, un sistema de identidad JA, antifraude, con marketplace regulado y
archivo histórico ministerial.

---

## Cómo encaja lo ya construido (21 sep 2026)

| Visión | Estado |
|---|---|
| CORE con API, RBAC por jurisdicción, auditoría | ✅ `api.adventist.club`: auth, usuarios, tutelas, árbol `ltree`, `audit_log` |
| Estructura División → Unión → Asociación | ✅ 992 entidades del Yearbook cargadas; clubes bajo la Asociación con aprobación del coordinador |
| Zona / Distrito → Iglesia → Club → Unidad | ⏳ el modelo lo soporta (tipos `zone`, `church`, `club`, `unit`); hoy el club cuelga directo de la Asociación. **Pendiente** exigir iglesia y zona al crear el club |
| Especialidades con revisión zonal → Asociación → publicación, versionado | ✅ flujo y versionado en la API; 809 especialidades con parche real |
| Cursos, exámenes, evidencias, portafolio | ⏳ banco de preguntas modelado; faltan inscripción, intentos, evidencias y portafolio |
| Certificados con hash + QR + verificación pública | ✅ en producción; motor de plantillas SVG; ⏳ firma criptográfica (`issuer_keys`) y anulación por fraude |
| Multilenguaje | ⏳ decidido (`I18N_Y_PLANTILLAS.md`); plantillas ya traducibles; falta UI y catálogo |
| Roles Secretaría, puntajes de clubes, invitación por enlace | ⏳ no iniciado |
| guiasmayores.app, sevenpxs.com, admin.adventist.club | ⏳ no iniciado (el CORE ya es multi-ministerio y multi-aplicación) |
| 2FA obligatorio para MASTER GC, doble aprobación | ⏳ TOTP disponible; falta hacerlo obligatorio y la doble aprobación |
