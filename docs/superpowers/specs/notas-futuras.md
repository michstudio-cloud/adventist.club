# Notas para el futuro (ideas del responsable, aún sin spec)

## Constructor visual de cursos por campo (22 sep 2026)
Idea del responsable al ver el portafolio en producción: hoy cada requisito ofrece un área de texto («Tu respuesta») o
una subida de foto según sea de respuesta o práctico (marca `is_theoretical`, clasificada automáticamente y corregible
por revisores). **Más adelante, cada campo lo define un instructor**: al revisar o desarrollar la especialidad decide,
requisito por requisito (e inciso por inciso), **qué se le pide al miembro** — respuesta corta, respuesta larga, opción
múltiple, foto, varias fotos, PDF, video, enlace, casilla de «lo hice frente a mi instructor» — con su texto de ayuda,
ejemplos y material, **como un constructor visual de cursos**. El miembro verá exactamente ese formulario.

Dónde encaja: bloque B (cursos del instructor virtual). El «plan de evaluación por requisito» de esa spec
(`REVIEW` / `EVIDENCE` / `EXAM`) es la semilla; el constructor lo amplía a un **esquema de campos por requisito**
(`course_requirement_fields`: tipo, etiqueta, ayuda, obligatorio, orden) y las respuestas del miembro pasan de una nota
libre (`requirement_progress.member_note`) a respuestas por campo. Debe diseñarse para que lo contestado hoy en
`member_note` y las evidencias actuales sigan siendo válidas (migración aditiva, sin perder nada).

Interfaz pedida para el miembro: enviar a revisión con **un solo botón de check** por requisito, no un botón grande.
