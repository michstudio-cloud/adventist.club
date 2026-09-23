# Ideas de referencia: app «Ablo» (capturas del propietario, 2026-09-22)

Ablo es una app social de "amigos por todo el mundo" (chat con desconocidos, ranking de fans,
vídeos). **No es un modelo a copiar** —su núcleo (chat 1:1 con extraños, popularidad
individual, vídeos de usuarios) choca con la protección de menores que rige este ecosistema
(ver `VISION_ECOSISTEMA.md`)—, pero varias de sus ideas de UX sí encajan si se traducen a
nuestro contexto. Esta nota deja la traducción hecha para no volver a discutirla.

## Lo que sí adoptamos

| Idea en Ablo | Traducción para conquistadores.app / adventist.club | Dónde encaja |
|---|---|---|
| **Globo terráqueo estilizado** (tierra verde plana, agua violeta con nubes, sin etiquetas al alejar) + «Toca para despegar» | El mapa de clubes en MapLibre GL con proyección *globe*: al abrir «Encuentra tu club» se ve el mundo y un toque «despega» hacia tu ubicación (flyTo). Look «ilustrado» con la paleta `cq-*` además del realista. | `/clubs`, `components/cq/club-map-canvas-maplibre.tsx` (en curso) |
| Filtro **«Mundial \| Ambos»** sobre el mapa | Chips de alcance sobre el mapa: **Mi asociación · Mi unión · Mundial**, usando la jerarquía División > Unión > Asociación que ya existe. | `/clubs` (pendiente) |
| **«¿Qué te gusta?»** (chips de intereses al registrarse) | Al crear la cuenta o al entrar por primera vez a Especialidades: elegir 3–5 **categorías de especialidades** (Naturaleza, Artes, Actividades misioneras, …) para ordenar el catálogo y recomendar la primera especialidad. Sin perfilado social; sólo ordena el catálogo. | Onboarding + `/categories` (pendiente) |
| **«Millas»** (puntos por actividad) | **Puntos de progreso** por especialidad completada, asistencia y horas de servicio (bloque F ya registra actividad). Son puntos de *crecimiento*, se muestran al propio conquistador y al director; nunca a extraños. | Portafolio / Panel (pendiente; la visión ya pide gamificación de club vía Secretaría) |
| **«¡Bienvenido a bordo!» + compartir** | Momento de bienvenida al aprobar tu ingreso al club (tarjeta compartible con el nombre del club, como ya se comparte un certificado). | Solicitudes de ingreso → aceptación (pendiente) |
| **Modo Chat / Modo Juego** | Sólo el **modo juego**: «Practicar» con preguntas del banco de la especialidad (bloque I4), sin calificación, para repasar antes del examen. | `components/courses/question-bank-sheet.tsx` reutilizado en modo práctica (pendiente) |

## Lo que NO adoptamos (y por qué)

- **Chat con desconocidos** («Ambos», modo chat): prohibido por la política de menores
  (sin mensajería privada adulto–menor; nada con extraños).
- **Ranking de «fans»** (quién es tu mayor fan): popularidad individual entre menores.
  Si algún día hay ranking, es **por club o unidad** (equipo), nunca por persona.
- **Vídeos subidos por los usuarios** («muéstrale al mundo tus alrededores»): los vídeos
  los suben instructores/directores (recursos de la especialidad); las fotos de evidencia de
  menores van al bucket privado y no se publican.
- **Compartir posición en tiempo real**: el mapa usa la ubicación sólo para buscar clubes
  (no se guarda, ya está así).

## Estado

- Globo + look ilustrado: en construcción como specimen en `/styleguide` (agente, 2026-09-22).
- El resto: ideas aceptadas, sin fecha; se priorizan después del lanzamiento por etapas.
