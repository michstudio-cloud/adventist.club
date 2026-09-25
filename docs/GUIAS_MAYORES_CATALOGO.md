# Guías Mayores · catálogo (insumo)

Documento de insumo para el catálogo de Guías Mayores que se construirá después (medallones,
maestrías, certificaciones de entrenamiento). **Solo documentación: no se cargan datos.**
Diseño del bloque F: `docs/superpowers/specs/2026-09-22-guias-mayores-aventureros-design.md`
(GM / EMC / CMJA son programas `CURRICULUM` con `issuer_level = 'ASSOCIATION'`, decisión D3).

## EMC — Entrenamiento para el Ministerio de Clubes (Club Ministry Training, CMT)
Fuente: mundoja.org (voluntarios Mundo J.A, DIA),
<https://mundoja.org/clubes/guias-mayores/emc> (consultada 2026-09-24). Resumen propio; los
nombres de talleres y sus códigos se copian tal cual porque son identificadores.

### Qué es
- Un **marco curricular de capacitación especializada para el personal del club** en funciones
  concretas (director, consejero, instructor, secretario/tesorero…). No es una clase de
  investidura como Rayos de Sol, Amigo o Guía Mayor: es una **certificación**.
- Cada certificación está pensada para **un año**, salvo el Entrenamiento Básico para Dirigentes.
- Se obtiene **asistiendo a talleres, trabajando con un mentor, haciendo trabajo de campo y
  preparando un portafolio** con documentos y evidencias de cada requisito.
- Las certificaciones EMC **reemplazan a Guía Mayor Avanzado y Guía Mayor Instructor**.
- El pin EMC **no es una investidura**; la investidura más alta del Ministerio de Clubes sigue
  siendo **Guía Mayor**.
- Cada certificación va **ligada a un rol, cargo o responsabilidad**: hay que estar sirviendo en
  el liderazgo de clubes de la iglesia local o de la Asociación/Misión.
- Al terminar, el pin o botón de la certificación se coloca en la banda.

### Certificaciones y temas (talleres con su código)
La página lista cinco certificaciones del área de Conquistadores (las imágenes
`/images/logos/certificaciones-EMC-1..5.png` de la página muestran sus pines; no se descargaron).

| # | Certificación | Talleres (código) |
|---|---|---|
| 1 | **Capacitación del personal de Conquistadores** (entrenamiento básico) | Ministerio del Club: Propósito e Historia (PFAD 001) · Organización de clubes (PFAD 002) · Programación y Planificación (PFAD 003) · Alcance del club (PFAD 004) · Ceremonias y Simulacro (PFAD 005) · Crecimiento del desarrollo (PYSO 104) · Introducción a la Enseñanza (EDUC 001) · Cuestiones médicas, de gestión de riesgos y de seguridad infantil (MEDI 100) |
| 2 | **Consejero de Conquistadores** | Alcance del Club de Conquistadores (PFAD 004) · La Organización del Club: el equipo de apoyo del consejero (PFAD 100) · Las Responsabilidades del Consejero (PFAD 101) · Crecimiento del desarrollo (PYSO 104) · Discipulado y Disciplina (PYSO 121) · La relación del consejero con el Conquistador (PYSO 124) · Seguridad y el Consejero (RCSF 120) · Aplicaciones Espirituales en la Naturaleza (NAOS 120) |
| 3 | **Instructor del Club de Conquistadores** | Estilos (EDUC 002) · Comprender el aprendizaje, Estilos (EDUC 003) · Trabajando con Niños con Necesidades Especiales (EDUC 006) · Enseñanza cristiana, Valores (EDUC 150) · Logro de Investidura Docente: Intención y Organización (EDUC 200) · Aplicaciones prácticas para la realización de la investidura docente (EDUC 210) · Enseñanza de honores (EDUC 230) · Introducción a la Disciplina (PYSO 120) |
| 4 | **Secretario / Tesorero del Club de Conquistadores** | Informes, Registros y Sistemas de Mérito (PFAD 140) · Desarrollo del Calendario Anual (PFAD 141) · Formularios: Salud y Médico, Permiso, Voluntario y Conductor de Vehículo (PFAD 142) · Introducción a la elaboración de presupuestos (FINA 101) · Finanzas del club (FINA 100) · Introducción a la recaudación de fondos (FINA 110) · Comunicación Práctica (CMME 104) |
| 5 | **Director del Club de Conquistadores** | Introducción a las Habilidades de Liderazgo (LEAD 001) · La Asociación y la Junta de Iglesia local (LEAD 002) · Introducción al personal de reclutamiento, selección y capacitación (LEAD 150) · Encuesta de Camping y Planificación del campamento (WILD 101) · Investidura Docente, Logro (EDUC 200) · Finanzas del club (FINA 100) · Introducción a la Disciplina (PYSO 120) · Trabajar y comunicarse con los padres (PYSO 207) |

Observaciones: los códigos (PFAD, PYSO, EDUC, MEDI, RCSF, NAOS, FINA, CMME, LEAD, WILD) son los
del currículo CMT de la NAD/GC; varios talleres se repiten entre certificaciones (PFAD 004,
PYSO 104, EDUC 200, FINA 100, PYSO 120): conviene modelarlos una vez y referenciarlos. La página
no publica el número de horas ni el contenido de cada taller; las certificaciones equivalentes para
el personal de **Aventureros** no aparecen en esta página.

### Cómo encaja en el modelo (propuesta, sin datos cargados)
- Cada certificación = un `programs` `kind = 'CURRICULUM'`, ministerio `master-guides`,
  `issuer_level = 'ASSOCIATION'`, `authority` según el manual que se use (DIA/GC), con
  **prerrequisito de rol** (cargo activo en `club_officers` / liderazgo de Asociación) — hoy no
  existe un tipo de requisito «rol»; iría como requisito `FREE` con evidencia o como regla de
  inscripción.
- Talleres = requisitos `FREE` con evidencia (asistencia) o, cuando exista F9, `COURSE`
  (reservado en 012). Mentor = mentoría `OPTIONAL` (diseño F, §mentoría); portafolio = evidencias
  del motor A.
- Pin EMC: certificado de tipo certificación (no investidura), plantilla distinta de
  `investidura-guia-mayor`.

## Pendiente para el catálogo de Guías Mayores
- Requisitos de **Guía Mayor** (ya cargados como borrador desde la Pathfinder Wiki:
  `backend/migrations/data/ay_classes.json`, slug `guia-mayor`).
- **Medallones y maestrías** (Maestría en …, Medallón de Plata / Oro / Plata-Oro): falta fuente;
  mundoja tiene secciones de Guías Mayores que no se revisaron en este encargo.
- Certificaciones EMC para personal de Aventureros y **CMJA**: sin fuente todavía.

## Cargado (024, 2026-09-24) — borrador
- `migrations/024_master_guide_catalog.sql`: `programs.kind` admite `MEDALLION`, `MASTERY`, `TRAINING`
  (ministerio `master-guides`); `users.active_ministry_id` / `users.active_club_id` (selector del armazón).
- `data/master_guide_catalog.json` (generado por `migrations/catalog_tools/build_master_guide_catalog.py`,
  sin red) y `migrations/import_master_guide_catalog.py [--commit] [--publish]` (idempotente por hash):
  - **TRAINING**: las 5 certificaciones EMC de arriba (`EMC-1`…`EMC-5`), 4 secciones cada una (servicio
    en el cargo · talleres con su código como `label` · mentoría y campo · portafolio), requisitos FREE con
    evidencia, `issuer_level ASSOCIATION`, `authority IAD`, fuente mundoja `/clubes/guias-mayores/emc`,
    autoría «Mundo J.A (voluntarios)».
  - **MASTERY**: 1 ejemplo «Maestría en Naturaleza (ejemplo, sustituir)». Fuente encontrada:
    <https://mundoja.org/maestrias> y <https://mundoja.org/component/content/article/1390-maestrias-2026>
    listan **17 maestrías como especialidades de Conquistadores** (categoría «Maestrías», ya en nuestro
    catálogo `masters`); requisitos por maestría en `…/article/1035…1051-maestria-*` (p. ej. 1041
    Naturaleza: 4 de flora, 2 de fauna, 1 de fauna doméstica). Decisión del propietario pendiente: ¿el
    certificado de maestría es esa especialidad o un programa de Guías Mayores?
  - **MEDALLION**: 1 ejemplo «Medallón (ejemplo, sustituir)»: ninguna página de mundoja
    `clubes/guias-mayores/*` (índice, guia-mayor, guia-mayor-avanzado, guia-mayor-instructor, emc) ni
    `emblemas/guias-mayores` lista medallones.
  - `--publish` publica sólo el EMC; un ejemplo nunca.
- La app los muestra en `/categories?vista=certificaciones` y `/clases` (ministerio Guías Mayores), ficha en
  `/certificaciones/[id]`.
