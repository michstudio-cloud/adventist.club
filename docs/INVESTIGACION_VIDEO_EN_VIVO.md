# Clases en vivo por video (estilo Zoom, hasta 100 personas): investigación y recomendación

> Investigación técnica del 22-sep-2026, con datos verificados en la web ese día (precios, límites,
> estado de mantenimiento). Donde un dato no pudo confirmarse se dice explícitamente. Es un documento
> para que el dueño DECIDA; no se ha construido nada todavía. Ver `VISION_ECOSISTEMA.md` §9 para el
> contexto de producto (clase de una especialidad en vivo desde admin.adventist.club).

## 0. Resumen

- **Tecnología recomendada: LiveKit.**
  - Open source (Apache-2.0), release el 14-sep-2026.
  - Componentes React para construir una UI propia con el sistema `cq-*`.
  - El backend FastAPI emite los tokens JWT.
  - Funciona igual en su nube que auto-alojado: cambiar de una a otra es cambiar una URL y unas llaves.
- **Camino A, cero servidores:** LiveKit Cloud plan **Ship**, unos **US$50/mes**. Peor caso (100 personas en
  cada clase): **US$70–120/mes** por exceso de ancho de banda.
- **Camino B, control total y costo fijo:** LiveKit auto-alojado (mejor con **OpenVidu Community**, que lo
  empaqueta) en Hetzner, **€86–139/mes** fijos. Hetzner más que duplicó el precio de sus VMs dedicadas el
  15-jun-2026. Con el volumen actual el auto-alojado NO es más barato que A; conviene pasados ~1,2 TB de
  tráfico de video al mes (unas 40–80 clases de 30 personas).
- **Para vigilar:** Cloudflare RealtimeKit. Precio por minuto sin cobro por ancho de banda, graba directo a
  R2, sala de espera y roles, hoy gratis. Pero sigue en beta (sin fecha de GA ni SLA) y no es open source.

## 1. Tabla comparativa

Supuestos de costo (estimaciones): típico = 8 clases × 60 min × 30 personas = 14.400 participante-minutos
al mes; peor caso = 8 × 60 × 100 = 48.000. Grabación: 480 min/mes. Ancho de banda de bajada supuesto:
0,5–1 GB por participante-hora (instructor en 720p + miniaturas) → típico ≈ 120–240 GB/mes, peor ≈ 400–800.

### 1a. Estado, capacidad y costo

| Opción | Open source / mantenimiento | ¿100 personas? | Costo típico / peor caso (mensual) |
|---|---|---|---|
| LiveKit Cloud | Servidor Apache-2.0, v1.13.7 (14-sep-2026); `@livekit/components-react` 2.9.24 (ago-2026) | Sí. Build (gratis): 100 conexiones simultáneas EN TOTAL y corte duro. Ship: 1.000 | Build no sirve (5.000 min, 50 GB, corte duro). **Ship: $50 / ~$70–120** (150k min y 250 GB incluidos; luego $0,12/GB). Grabación: 600 min incluidos |
| LiveKit auto-alojado / OpenVidu Community | Igual; OpenVidu es un fork 100 % compatible con Egress, S3 y monitoreo incluidos | Sí. Benchmark oficial: 150 personas con cámara en 16 núcleos al 85 %. Para 100 con pocas cámaras: 4–8 núcleos dedicados (estimación) | Hetzner CCX23 (4 dedicados) €85,99; CCX33 (8 dedicados, 30 TB) €138,49 (precios desde 15-jun-2026; tráfico extra €1/TB en la UE) |
| Jitsi auto-alojado | Apache-2.0, stable 11248 (14-sep-2026) | Sí, con matices: Prosody usa un solo núcleo y se degrada cerca de 100 | Parecido a LiveKit. Grabar requiere Jibri: una instancia pesada por grabación simultánea |
| JaaS (Jitsi por 8x8) | De pago, basado en Jitsi | Hasta 500 por reunión | Cobra por usuario único al mes (MAU): gratis 25; Basic $99 (300); Standard $499 (1.500); exceso $0,99/MAU. Grabación $0,01/min. Crece con alumnos distintos, no con horas; cada dispositivo cuenta |
| BigBlueButton | LGPL-3.0, 3.0.37 (10-sep-2026); 4.0 en RC | Sí, pensado para clases | Requisitos oficiales: 8 núcleos, 16 GB, 500 GB, 250 Mbps, Ubuntu 22.04, servidor dedicado → ≈ CCX33 €138,49/mes o más |
| mediasoup | ISC, 3.27.1 (16-sep-2026), muy activo | Técnicamente sí | Solo la VM, pero meses de desarrollo: es una librería SFU sin salas, señalización, grabación ni UI |
| Daily | Propietario; `daily-react` activo (sep-2026) | Sí; "large calls" hasta 1.000 | 10.000 min gratis, luego $0,004/min. ≈ $24 / ≈ $159 |
| 100ms | Propietario; `web-sdks` activo; `100ms-web` archivado | Probable; máximo por sala no encontrado | 10.000 min gratis, luego $0,004/min; grabación $0,0135/min. ≈ $20 / ≈ $155 |
| Cloudflare RealtimeKit (ex-Dyte) | Propietario, BETA, gratis hoy; SDK de Dyte en mantenimiento | Máximo por reunión no documentado | Hoy $0. Tarifas para GA: $0,002/min con video; grabación $0,010/min. ≈ $34 / ≈ $101. Sin nivel gratuito ni fecha de GA confirmados |

### 1b. UI propia, autenticación, moderación y operación

| Opción | ¿UI propia? | Auth con tokens del backend | Moderación / menores | Carga operativa |
|---|---|---|---|---|
| LiveKit (Cloud o propio) | **Sí**: hooks y componentes React; vista prearmada opcional | JWT con API key/secret. Permisos: roomJoin, canPublish, canPublishSources, canPublishData, canSubscribe, hidden, roomAdmin; metadata/attributes para rol, club | Por API: expulsar, silenciar, cambiar permisos en vivo. Sin sala de espera hecha: se implementa en el backend. Grabación (Egress) a S3, R2 incluido | Cloud: ninguna. Propio: servidor con parches, TLS, UDP |
| Jitsi | Solo en la práctica: iframe/React SDK con la interfaz de Jitsi; UI propia solo con lib-jitsi-meet | JWT vía Prosody | Lobby sí; disablePrivateChat; silenciar/expulsar | Alta: Prosody + Jicofo + JVB + Jibri |
| JaaS | Igual que Jitsi (iframe) | JWT con moderador | Grabación guardada solo 24 h en 8x8; descargar por webhook | Ninguna |
| BigBlueButton | **No**: app completa; solo tema/logo/textos | URL firmada por usuario | La mejor de fábrica para menores (sin chat privado, sala de espera, muteOnStart, pizarra, grabación). Grabación en disco local; no va a R2 sin trabajo extra | Alta: pila grande y actualizaciones mayores |
| mediasoup | Todo a mano | Todo a mano | Todo a mano | Muy alta |
| Daily | Sí (daily-react) + Prebuilt | Meeting tokens | Knocking, permisos, grabación a S3 propio (AWS/Oracle; R2 no confirmado) | Ninguna |
| 100ms | Sí (React SDK) + Prebuilt | Tokens con roles | Roles y plantillas (no verificado en detalle) | Ninguna |
| RealtimeKit | Sí: Core SDK o UI Kit | Reunión y participante por REST | Muy completo: sala de espera, expulsar, silenciar, chat por preset/rol, grabación directo a R2 | Ninguna, pero beta |

## 2. Recomendación

**LiveKit, con UI propia hecha con `@livekit/components-react` y el sistema `cq-*`.** Es la única opción que
cumple a la vez: open source con mantenimiento activo; UI 100 % propia (cuadrícula, tira de participantes,
chat lateral, controles); y mudarse entre hospedado y auto-alojado sin reescribir. Jitsi y BigBlueButton son
"Zoom open source" en sentido estricto, pero imponen su interfaz y exigen servidores pesados.

**Camino A, cero servidores (recomendado para empezar):** LiveKit Cloud Ship — típico US$50; peor caso
≈ US$70–120; grabación dentro de lo incluido.

**Camino B, control total y costo fijo:** OpenVidu Community en Hetzner CCX33 — €138,49 (≈ US$163) fijos;
variante CCX23 €85,99 si solo el instructor y pocos alumnos encienden cámara. Conviene pasados ~1,2 TB/mes.
Evitar VMs de CPU compartida para video en vivo (vecino ruidoso = cortes).

**Alternativa a probar en paralelo:** Cloudflare RealtimeKit (ya usan Cloudflare/R2, por minuto sin cobro por
ancho de banda, sala de espera y roles de fábrica, graba a R2, gratis hoy) — pero beta, propietario, y con el
precedente de Dyte. Sirve para un piloto, no como base todavía.

Daily y 100ms son razonables con uso bajo (~$20–24) pero cuestan más en el peor caso (~$155) y no son open
source.

## 3. Cómo encaja con el stack actual

**Infraestructura nueva.** Camino A: ninguna, solo una cuenta de LiveKit Cloud (el video va del navegador a
LiveKit; Vercel y Render no mueven video). Camino B: una VM, un subdominio (p. ej. live.conquistadores.app)
con TLS, puertos UDP 50000–60000 y TURN/443. En ningún caso el servidor de medios puede vivir en Render
(0,15 CPU) ni en Vercel (serverless no maneja WebRTC por UDP).

**Backend FastAPI** (trabajo liviano):
- Tablas: `live_sessions` (course_id, club_id, instructor_id, room_name aleatorio, estado, horario,
  recording_enabled, recording_r2_key); `live_session_attendance` (entrada, salida, duración; se llena con
  webhooks y puede alimentar la asistencia de la especialidad); opcionales `live_session_admissions` (sala de
  espera) y `live_chat_messages` (auditoría).
- `POST /live-sessions`: el instructor agenda la clase ligada a un curso.
- `POST /live-sessions/{id}/token` (con la cookie JWT actual): comprueba inscripción o instructor; emite un
  token LiveKit de vida corta con `livekit-api` (identity=user_id, name, metadata con el rol). Alumnos:
  canSubscribe, sin canPublishData, cámara/mic desactivados hasta que el instructor los habilite. Instructor:
  roomAdmin.
- Sala de espera: el alumno queda "pendiente" en la base; el token solo se emite cuando el instructor lo
  admite (o entra sin permisos y se le conceden con UpdateParticipant).
- Moderación: endpoints del instructor → RemoveParticipant (y bloquear la reemisión del token),
  MutePublishedTrack / quitar canPublishSources, dar o quitar la palabra.
- **Chat sin mensajes privados, garantizado del lado del servidor:** los alumnos NO tienen canPublishData;
  el chat va a FastAPI, que lo valida, lo guarda (auditado) y lo difunde a toda la sala con SendData. Un
  cliente modificado no puede mandar mensajes privados adulto-menor.
- Grabación: Egress RoomComposite → R2 (force_path_style) en bucket PRIVADO; se sirve con URLs firmadas.
- `POST /webhooks/livekit`: verifica firma; registra participant_joined/left, room_finished, egress_ended.

**Frontend Next.js 16:** ruta tipo `/cursos/[id]/en-vivo` (client component) que pide el token y monta
`<LiveKitRoom>`; UI propia con hooks (useTracks, useParticipants) y componentes de bajo nivel
(ParticipantTile, VideoTrack, TrackToggle) con `cq-*`: cuadrícula con instructor destacado y paginación,
tira de participantes, chat lateral (contra FastAPI), controles, panel del instructor (admitir, expulsar,
silenciar a todos, grabar). Activar simulcast y adaptive stream (muchos alumnos en móvil).

## 4. Riesgos y lo que NO conviene hacer

- No usar niveles gratuitos públicos en producción: LiveKit Build corta en seco; JaaS gratis son 25 MAU;
  meet.jit.si incrustado se corta a los 5 minutos (desde 2023); y un servicio público no controla quién entra
  — inaceptable con menores.
- No construir sobre mediasoup directo (meses de trabajo para un equipo pequeño).
- No fiarse de restricciones solo en la UI: chat privado, permisos y acceso van en el token o el servidor.
- No dejar salas con nombre adivinable ni tokens de larga vida.
- Grabar menores exige política: consentimiento de los padres, aviso visible de "grabando", retención y
  borrado definidos, bucket privado. Recomendación adicional: regla de "dos adultos" (segundo moderador),
  práctica habitual en ministerios juveniles — confirmar la política oficial de la asociación.
- Latencia / región (NO verificado): Hetzner no tiene centros en Latinoamérica (los más cercanos: Ashburn y
  Hillsboro, EE. UU.); no se pudo confirmar si LiveKit Cloud tiene servidores de medios en Sudamérica.
  Probar con usuarios reales en la región antes de comprometerse.
- Incertidumbres explícitas: los GB por participante-hora (0,5–1) son estimación, no dato de proveedor — es
  la variable que más mueve el costo; medir en un piloto. No se confirmó el máximo por sala de 100ms ni de
  RealtimeKit, ni si Daily graba a R2, ni precios de BigBlueButton hospedado, ni el CPU exacto para grabar en
  auto-alojado (Egress usa Chrome sin interfaz).

## Fuentes

- LiveKit: precios (livekit.com/pricing) · cuotas y límites (docs.livekit.io/home/cloud/quotas-and-limits/)
  · benchmark (docs.livekit.io/home/self-hosting/benchmark/) · despliegue
  (docs.livekit.io/home/self-hosting/deployment/) · tokens y permisos
  (docs.livekit.io/frontends/reference/tokens-grants/) · salidas de Egress
  (docs.livekit.io/transport/media/ingress-egress/egress/outputs/) · RoomService API
  (docs.livekit.io/reference/other/roomservice-api/)
- OpenVidu: openvidu.io/pricing/ · openvidu.io/3.8.0/docs/self-hosting/deployment-types/
- Hetzner: docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/ ·
  hetzner.com/cloud/general-purpose/ · costgoat.com/pricing/hetzner
- Jitsi: jitsi.github.io/handbook/docs/devops-guide/devops-guide-requirements/ ·
  jitsi.github.io/handbook/docs/devops-guide/token-authentication/ · aviso de corte al incrustar
  (community.jitsi.org/t/.../123003) · JaaS precios (cpaas.8x8.com/en/pricing/jitsi-as-a-service-pricing/)
  · JaaS FAQ (developer.8x8.com/jaas/docs/faq/)
- BigBlueButton: docs.bigbluebutton.org/administration/install/ ·
  docs.bigbluebutton.org/administration/customize/
- Daily: daily.co/pricing/video-sdk/ · docs.daily.co/guides/scaling-calls/large-real-time-calls ·
  docs.daily.co/guides/products/live-streaming-recording/storing-recordings-in-a-custom-s3-bucket
- 100ms: 100ms.live/pricing
- Cloudflare RealtimeKit: developers.cloudflare.com/realtime/realtimekit/pricing/ · realtime.cloudflare.com
  · .../concepts/preset/ · .../concepts/meeting/ · .../recording-guide/custom-cloud-storage/ ·
  blog.cloudflare.com/introducing-cloudflare-realtime-and-realtimekit/ · .../release-notes/web-core/
- Versiones y fechas de mantenimiento tomadas de la API de GitHub y de npm el 22-sep-2026.
