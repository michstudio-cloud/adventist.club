# Clases en vivo: costos vía API/embebido de las 10 opciones pedidas por el dueño

> Investigación del 22-sep-2026. **Complementa** a `INVESTIGACION_VIDEO_EN_VIVO.md` (LiveKit, Jitsi,
> BigBlueButton, Daily, 100ms, Agora, Twilio, YouTube Live, RealtimeKit): no repite lo que ya está ahí,
> solo lo extiende con las opciones que salieron en las búsquedas del dueño. Cada precio lleva su fuente
> (ver [Fuentes](#fuentes), referencias `[Fn]`). Cuando un dato NO se pudo verificar en una fuente oficial
> se dice explícitamente. Es un documento para DECIDIR; no se construyó nada.

## 0. Resumen ejecutivo

- **Escenario:** 1 clase de hasta 100 personas, 60 min, 2–4 veces por semana → 9–17 clases/mes →
  54.000–102.000 participante-minutos/mes. Presupuesto: US$0–100/mes. Muchos participantes son menores.
- **Nadie da "Zoom embebido para 100, con grabación, gratis".** Hay que elegir entre embebido (cuesta) o
  abrir el enlace del proveedor en otra pestaña (barato).
- **Más barato con interacción: Google Meet.** Con Google Workspace for Nonprofits cuesta **US$0**; Business
  Standard con descuento para ONG, **US$3,50/usuario/mes**, incluye grabación [F9]. Tiene API REST (crear
  salas, asistencia, grabaciones), pero exige una cuenta Workspace, no Gmail [F11]. **No se puede
  embeber**: la app guarda el enlace y lo abre aparte.
- **Mejor embebido sin servidores: JaaS (Jitsi de 8x8).** US$99/mes hasta 300 usuarios únicos, más
  grabación a US$0,01/min → **≈ US$104–109/mes** [F1]. Va en un iframe dentro de nuestra app, con JWT emitido
  por FastAPI, sala de espera y sin chat privado. Queda en el tope del presupuesto, y cada dispositivo cuenta
  como un usuario.
- **Clase magistral de 100: YouTube Live, US$0.** Se embebe con iframe [F26–F29], pero es de una sola vía.
  El contenido para Conquistadores probablemente es "hecho para niños", lo que quita chat y comentarios.
  Para menores eso es bueno: el chat lo ponemos nosotros, moderado.
- **Idea nueva: Jitsi o BigBlueButton propios, encendidos solo durante la clase** (Hetzner cobra por hora:
  CCX33 a €0,2219/h). Cómputo de **≈ €3–6/mes**, con embebido real [F20]. Exige automatizar el arranque y el
  apagado, y alguien técnico.
- **Descartadas:**
  - Nextcloud Talk: sin API de embebido; el Talk Enterprise es de mínimo 100 usuarios, ≈ €350/mes.
  - Element Call: exige cuentas Matrix y un servidor Synapse; si se quiere esa tecnología, mejor LiveKit
    directo.
  - Teams embebido: US$214–404/mes vía Azure Communication Services.
  - Zoom Video SDK: US$154–322/mes.
  - Zoho AV SDK: US$110–230/mes.
- **Zoom Pro:** barato (US$14,16–16,99 por anfitrión al mes) y conocido, pero sus términos dicen que el
  servicio no es para menores de 16 salvo cuentas escolares K-12 [F17]. Es un riesgo legal para clubes de
  10–15 años.
- **Top 3:**
  1. Google Meet vía Workspace para ONG: US$0–3,50.
  2. JaaS embebido: ≈ US$99–109.
  3. YouTube Live con chat propio: US$0.
- **Plan mínimo (US$0/mes):**
  - Clase de 100 por YouTube Live no listado, embebido en la app, con chat propio moderado.
  - Interacción en grupos chicos por Google Meet (Workspace para ONG).
  - Si no hay elegibilidad de ONG: Gmail gratis (máx. 60 min) o Zoho Meeting Standard 100 a US$6–7/mes con
    grabación.
- **Qué construir:** un modelo "proveedor + enlace" independiente del proveedor (provider, join_url,
  embed_url, recording_url, ventana de acceso, consentimiento), con el chat moderado en nuestro backend.
  Unos 6–9 días. LiveKit, JaaS o BigBlueButton quedan como proveedores futuros sin rehacer nada.

## 1. Supuestos (todo cálculo usa esto)

- 1 clase simultánea, 60 min, 100 personas conectadas (peor caso), 2–4 por semana → **9–17 clases/mes**.
- Participante-minutos/mes: 100 × 60 × 9 = **54.000** a 100 × 60 × 17 = **102.000**.
- Minutos grabados/mes: **540–1.020**.
- Usuarios únicos/mes (para JaaS, que cobra por MAU): **~100–150** si siempre son los mismos 100 alumnos
  (algunos entran desde dos dispositivos); **300–1.000+** si cada clase es de clubes distintos.
- Conversión aproximada: 1 € ≈ US$1,18 (la misma del doc anterior; no re-verificada hoy).
- "Costo realista" = licencias/uso del proveedor. No incluye horas de desarrollo ni impuestos.

## 2. Tabla comparativa

Columnas: **Emb.** = ¿se puede embeber en nuestra app? · **Esf.** = esfuerzo de integración en días de
desarrollo · **Menores** = protecciones disponibles (sala de espera, sin chat privado, consentimiento).

| Opción | Modelo | Costo mensual realista (1 clase ≤100, 2–4/sem) | Límite participantes | Emb. | Grabación | Menores | Esf. | Veredicto |
|---|---|---|---|---|---|---|---|---|
| **1a. Jitsi propio 24/7** | Self-host | Hetzner CCX23 €85,99 ≈ **US$101** sin grabar; con Jibri en 2.ª VM 24/7 ≈ **US$203** [F20] | ~100 en la práctica (Prosody usa 1 núcleo) [F4] | **Sí**: iframe API / React SDK en nuestro dominio; UI propia solo con lib-jitsi-meet [F5] | Jibri: 1 instancia (servidor pesado) por reunión grabada [F4]; subir a R2 con script | Lobby, JWT (solo usuarios de la app), `disablePrivateChat`, moderadores | 6–10 + operación | **Viable con reservas**: fuera de presupuesto 24/7 |
| **1b. Jitsi propio bajo demanda** | Self-host por hora | CCX33 €0,2219/h × 13,5–25,5 h = **€3–5,7 (≈ US$4–7)** + IP fija y snapshot (**no verificados**) → estimar **US$5–15** [F20] | ~100 | **Sí** (igual que 1a) | Jibri en otra VM por hora (CCX23 €0,1378/h → ≈ €2–4) | Igual que 1a | 8–12 | **Viable con reservas**: el embebido real más barato; exige automatizar |
| **1c. JaaS (8x8)** | SaaS + API | Basic **US$99** (300 MAU) + grabación 540–1.020 min × US$0,01 = **US$104–109**. Si hay 400 MAU: ≈ US$198; más de 1.500: US$499 [F1] | 500 por reunión [F2] | **Sí**: iframe `8x8.vc` / React SDK + JWT firmado por FastAPI [F3] | US$0,01/min; el archivo queda **solo 24 h** en 8x8, hay que copiarlo a R2 por webhook [F2] | Lobby, moderador por JWT, config sin chat privado; términos sobre menores **no verificados** | 3–5 | **Viable** (tope del presupuesto) |
| **2a. BigBlueButton propio 24/7** | Self-host | CCX33 €138,49 ≈ **US$163** (requisito oficial: 8 núcleos, 16 GB, Ubuntu 22.04, dedicado) [F6][F20] | Pensado para clases | Solo URL de ingreso firmada; iframe posible si el servidor lo permite; UI no es nuestra | Sí, en disco local; mover a R2 aparte | **La mejor de fábrica**: `guestPolicy=ASK_MODERATOR`, `lockSettingsDisablePrivateChat`, `muteOnStart`, `maxParticipants`, roles [F7] | 5–8 + operación | **No viable** por costo 24/7 |
| **2b. BigBlueButton bajo demanda** | Self-host por hora | CCX33 €3–5,7 de cómputo + IP y snapshot (no verificados) → **US$5–15** | Igual que 2a | Igual que 2a | Copiar a R2 **antes** de apagar el servidor | Igual que 2a | 8–12 | **Viable con reservas** |
| **2c. BigBlueButton hospedado** | SaaS + API | MynaParrot Ultra-1 **US$89** (100 concurrentes, máx. 100 por sala, API y MP4) [F8]. Blindside Networks y bbbserver: **sin precio público** | 100 por sala en ese plan | Igual que 2a (iframe según proveedor, **no verificado**) | Incluida (150 GB) | Igual que 2a | 3–5 | **Viable con reservas**: proveedor chico; revisar dónde guarda datos y la latencia |
| **3. Nextcloud Talk** | Self-host (+HPB) o Enterprise | Talk Enterprise €42/usuario/año con **mínimo 100 usuarios = €4.200/año ≈ US$413/mes** [F21]. Propio: Nextcloud + HPB (signaling + Janus + NATS + coturn) ≥ US$101/mes en VM [F22][F20] | Sin HPB: ~4–6 con video (comunidad), "20" solo audio; con HPB: 30–50 activos, cientos pasivos [F23] | **No**: sin SDK para apps externas; Nextcloud bloquea el iframe por CSP (hay que parchear) | Servidor de grabación aparte (no verificado) | Salas públicas con enlace de invitado; moderación básica | 15+ | **No viable** |
| **4. Element Call (Matrix)** | Self-host | ≥ costo de LiveKit propio (doc anterior) + Synapse; exige Synapse con MSCs, LiveKit y un servicio de autorización MatrixRTC [F24] | Grande vía SFU LiveKit; sin número publicado | Solo como widget de clientes Matrix; el modo standalone exige cuentas Matrix (registro abierto) [F24] | No documentada | Sala de espera y silenciar a otros **no documentados** | 15+ | **No viable**: si se quiere esto, LiveKit directo |
| **5a. Google Meet – Gmail gratis** | SaaS, sin API | **US$0** | 100; **60 min** con 3+ personas [F12] | **No** (no se puede iframear; solo enlace) [F13] | No | Acceso por "pedir unirse"; pocos controles | 0,5–1 (link manual) | **Viable con reservas**: solo clases de <60 min, sin API ni grabación |
| **5b. Google Meet – Workspace para ONG** | SaaS + REST API | **US$0** (Nonprofits) o **US$3,50/usuario/mes** anual (US$4,20 mensual) en Business Standard para ONG, con grabación [F9] | 150 según la página para ONG [F9] (otras fuentes dicen 100: confirmar) | **No** (enlace); el SDK de complementos de Meet mete NUESTRA app dentro de Meet, no al revés [F14] | Business Standard o superior, en Drive; la API da grabaciones y transcripciones [F11] | Acceso restringido, knocking, controles del anfitrión [F15]; chat solo grupal (no re-verificado) | 1 (link) / 4–6 (API + OAuth) | **Viable** si la organización es elegible [F10] |
| **5c. Google Meet – Workspace pago** | SaaS + REST API | Starter **US$7** (100, sin grabar) · Standard **US$14** (150, graba) · Plus **US$22** (500) por usuario/mes; anual ahorra 16 % [F9b] | 100 / 150 / 500 | No | Standard o superior | Igual que 5b | 1 / 4–6 | **Viable** (Standard: US$14/instructor) |
| **6. Microsoft Teams** | SaaS + Graph API | Business Basic **US$7/usuario/mes** desde 1-jul-2026 [F30]; donación de hasta 300 licencias Basic para ONG, con elegibilidad de iglesias **no clara** [F31][F32]. Embebido vía Azure Communication Services: 99 × 60 × 9–17 × US$0,004 = **US$214–404** [F33] | 300, 30 h (fuente secundaria) [F34] | **No** como iframe; **Sí** con el SDK/UI Library de ACS (caro) [F35] | Incluida en planes pagos (OneDrive/SharePoint) | Anónimos entran al lobby por defecto; el admin puede prohibir anónimos [F36] | 4–6 (link + Graph) / 10–15 (ACS) | **Viable con reservas** como enlace si ya hay tenant M365; **no viable** embebido |
| **7. Cisco Webex** | SaaS + REST + Browser SDK | Free: 100 personas, **40 min** [F37]. Meet ≈ **US$14,50/mes** (US$12 anual): **no verificado** en la página oficial, que en HTML estático solo muestra Free y Enterprise; el dato viene de terceros [F38] | Free 100; Meet 200 (terceros) | **Sí**: Browser SDK (UI propia) o Meetings Widget, con tokens de invitado vía Service App [F39][F40] | Nube en planes pagos | Lobby / bloqueo (no re-verificado); términos sobre menores **no verificados** | 6–10 | **Viable con reservas**: precio no oficial, poca adopción local, SDK complejo |
| **8. Zoho Meeting** | SaaS + API / AV SDK | Standard 100 participantes **US$7/mes (US$6 anual)** por anfitrión, con grabación (5 GB). Professional 100 **US$12 (US$10 anual)**: API, widget de embebido, salas de grupos [F41]. AV SDK: 10.000 min gratis y luego US$0,0025/min → **US$110–230** [F42][F43] | Free 100 / **60 min**; planes pagos hasta 250 | Widget de acceso (Professional, 1 código por reunión) [F44]; video dentro de la app solo con AV SDK | Standard y superior | Silenciar, bloquear reunión, moderador; términos sobre menores **no verificados** | 3–5 (API + link) / 10–15 (SDK) | **Viable con reservas**: el SaaS pago más barato con grabación |
| **9a. Zoom Pro** | SaaS + Meeting SDK | Pro **US$16,99/mes** o **US$14,16/mes** anual por anfitrión [F16]. Free: 100 personas, 40 min | 100, 30 h, 10 GB en la nube [F16] | Meeting SDK web: vista de cliente o de componentes (esta última solo escritorio) [F18]; se puede usar con app OAuth de cuenta gratis [F19]; desde 2-mar-2026 las apps que entran a reuniones de otras cuentas deben autorizarse (ZAK/OBF) [F18b] | Nube 10 GB (Pro) | Sala de espera y restricciones de chat (funciones estándar, no re-verificadas). **Términos: no es para menores de 16 salvo cuentas escolares K-12** [F17] | 1 (link) / 5–8 (SDK) | **Viable con reservas**: barato, pero con riesgo legal con menores |
| **9b. Zoom Video SDK** | API (UI propia) | 10.000 min gratis/mes y luego US$0,0035/min → (54.000−10.000) × 0,0035 = **US$154** a **US$322** [F25] | Hasta 1.000 por sesión [F25] | **Sí**, UI 100 % propia | Nube US$0,01/min (fuente secundaria) | Todo a nuestro cargo (como LiveKit) | 10–15 | **No viable** por costo (LiveKit es más barato; ver doc anterior) |
| **10. YouTube Live** | SaaS + Data API v3 | **US$0**. La cuota de API alcanza: 10.000 unidades/día; crear transmisión, stream, vincular y cambiar de estado cuesta 50 cada una (≈ 250–300 por clase) [F26] | Sin límite práctico de espectadores | **Sí**: iframe (público o **no listado** + "permitir inserción") | Queda archivada en el canal (límites de archivo no verificados) | Canal verificado por teléfono y hasta 24 h para activar [F27][F28]; si es "hecho para niños": **sin chat en vivo, sin comentarios, sin notificaciones** [F29]; si un menor de 16 sale en cámara, debe verse a un adulto [F28] | 1–2 (URL manual) / 4–6 (API) | **Viable** para clase magistral; **no** para interacción |

Para comparar (del doc anterior): con este escenario más cargado (hasta 17 clases de 100 personas), LiveKit
Cloud Ship quedaría en ≈ US$74–224/mes. Son US$50 más el exceso de ancho de banda a US$0,12/GB, suponiendo
0,5–1 GB por participante-hora (estimación, no dato del proveedor).

## 3. Notas por opción (solo lo nuevo o lo que cambia la decisión)

**1. Jitsi / JaaS.**
- **Qué se cobra:** JaaS cobra por usuario activo al mes (MAU) contado **por dispositivo**: guarda un
  identificador en el navegador, así que teléfono + laptop = 2 [F2]. Planes [F1]:
  - Developer: 25 MAU gratis.
  - Basic: US$99 (300 MAU).
  - Standard: US$499 (1.500 MAU).
  - Business: US$999 (3.000 MAU).
  - Exceso: US$0,99 por MAU.
  - Grabación: US$0,01/min. Transmisión RTMP: US$0,01/min.
- **Sin confirmar:** si el plan Developer permite pasar de 25 MAU pagando exceso o corta. La FAQ solo dice
  que el exceso se cobra con tarjeta registrada.
- **Integración:** iframe con `https://8x8.vc/external_api.js`, más `configOverwrite` para personalizar [F3].
  El backend firma el JWT (moderador = instructor).
- **Grabación:** 8x8 la borra a las 24 h; hay que bajarla a R2 con el webhook.
- **Jitsi propio:** 4 núcleos dedicados "pueden bastar" y 8 GB de RAM. Prosody usa un solo núcleo. Jibri
  graba una reunión por instancia y no debe correr en el mismo servidor [F4].

**2. BigBlueButton.**
- **API:** REST firmada con checksum (SHA-1/SHA-256 de la llamada + query + secreto). `create` acepta
  `guestPolicy`, `record`, `maxParticipants`, `muteOnStart`, `lockSettingsDisablePrivateChat/PublicChat`
  y `disabledFeatures` (reemplaza a `breakoutRoomsEnabled` en 3.0). `join` recibe `role`
  (MODERATOR/VIEWER). `getRecordings` lista las grabaciones [F7].
- **Funciones de aula:** pizarra, encuestas, salas de grupos y notas compartidas vienen de fábrica.
- **Requisito oficial:** Ubuntu 22.04, 8 núcleos, 16 GB, 500 GB, 250 Mbps y servidor dedicado [F6].
- **Hospedaje:**
  - Blindside Networks (sus creadores): solo "Request Info" [F8b].
  - bbbserver: precios fuera de la página [F8c].
  - MynaParrot: publica US$89 por 100 concurrentes [F8].
  - HigherEdLab: US$149, pero su dato es de 2021 [F8d]; no usarlo.

**3. Nextcloud Talk.**
- **Para qué sirve:** una suite de colaboración, no un componente para otra app. Los invitados entran por
  enlace a una conversación pública *dentro* de Nextcloud. No hay SDK de embebido, y el iframe está
  bloqueado por la CSP de Nextcloud (se puede parchear, sin soporte).
- **Backend de alto rendimiento (HPB):** necesario para pasar de unos pocos participantes. Es
  `nextcloud-spreed-signaling` (AGPL-3.0, activo sep-2026) más Janus, NATS y TURN [F22].
- **Veredicto:** sumaría un Nextcloud entero para usar solo el video. No viable.

**4. Element Call / MatrixRTC.**
- **Qué exige:** AGPL-3.0, v0.26.0 del 15-sep-2026. Necesita un homeserver Synapse con varias MSCs
  experimentales, LiveKit y un servicio de autorización MatrixRTC. Para usuarios sin cuenta, además, un
  homeserver con **registro abierto** [F24].
- **Veredicto:** tiene la complejidad de LiveKit más la de Matrix, sin ganar nada para una clase. No
  viable.

**5. Google Meet.**
- **API REST:** crea espacios, configura acceso, moderación y grabación automática, y da conferencias,
  asistentes, grabaciones y transcripciones [F11].
- **Requisito:** el quickstart oficial pide "A Google Workspace account with Google Meet enabled" [F11b].
  Con Gmail gratis no hay API ni grabación.
- **Embebido:** Meet no se puede meter en un iframe [F13]. El modelo práctico es:
  1. El instructor (o el backend vía API) crea la sala.
  2. La app guarda el enlace.
  3. El botón "Entrar" lo abre en una pestaña nueva.
- **Google for Nonprofits:**
  - Admite iglesias con estatus 501(c)(3) en EE. UU.
  - En Latinoamérica figuran Argentina, Chile, Colombia, México y Perú [F10].
  - La verificación la hace Goodstack.
  - **Acción recomendada:** preguntar si la asociación o unión ya tiene Google Workspace (muchas lo
    tienen). Entonces cuesta US$0 y no hay que tramitar nada.

**6. Microsoft Teams.**
- **Graph `onlineMeetings`:** con permiso de aplicación (`OnlineMeetings.ReadWrite.All`) el administrador
  del tenant debe crear una *application access policy* por usuario organizador. Devuelve `joinWebUrl` y
  `lobbyBypassSettings` [F35b].
- **Embeber:** solo con Azure Communication Services. Los externos pagan US$0,004 por minuto, también en
  el lobby [F33]; es de lejos lo más caro para 100 personas. Por el enlace normal de Teams, los externos no
  pagan [F33].
- **Donación de Microsoft para ONG:** retiró las licencias Premium/E1 gratuitas en jul-2025 y mantiene hasta
  300 Business Basic donadas [F31]. Su página de elegibilidad no nombra a organizaciones religiosas [F32]:
  hay que preguntar.

**7. Webex.**
- **API y embebido:** la API REST de reuniones requiere un usuario con licencia de Webex Meetings [F39b]. El
  Browser SDK permite UI propia, y el Meetings Widget es la vía rápida [F40].
- **Plan gratis:** 40 min, así que no sirve para clases de 60.
- **Precio pago:** no se pudo leer en la página oficial.

**8. Zoho Meeting.**
- **Precios oficiales:** leídos del JSON de precios que usa zoho.com/meeting/pricing.html [F41]. Standard
  100 cuesta US$7 mensual o US$6 anual. Professional 100 cuesta US$12 o US$10.
- **Embebido:** "Embed meeting widget" y "API access" son exclusivos de Professional [F41][F44].
- **AV SDK:** tiene precio aparte. La ayuda dice que los 10.000 minutos se acreditan "initially"; la página
  del SDK dice que son "por mes" [F42][F43]. **Contradictorio**: confirmar con Zoho.

**9. Zoom.**
- **Meeting SDK:** la vista de componentes es solo para escritorio; en móvil hay que usar la vista de
  cliente [F18].
- **Cambio de 2026:** desde el 2-mar-2026, una app que entra a reuniones fuera de su propia cuenta necesita
  un token ZAK u OBF [F18b]. Para nuestro caso, las reuniones deberían ser de la cuenta de la organización.
- **Video SDK:** 10.000 min gratis/mes y luego US$0,0035/min; hasta 1.000 personas [F25]. Una fuente
  secundaria dice que exige comprar créditos (US$100 = 100 créditos) en vez de pago por uso puro; **no
  confirmado**.
- **Menores:** "Zoom is not intended for use by individuals under the age of sixteen", salvo cuentas
  escolares K-12 [F17].

**10. YouTube Live.**
- **Activación:** hay que verificar el canal y no tener restricciones en 90 días. La primera activación
  puede tardar hasta 24 h [F27][F28].
- **API:** crear una transmisión exige un codificador (OBS, RTMP). Si el instructor transmite con la
  webcam desde YouTube Studio, lo más simple es pegar la URL en la app.
- **"Hecho para niños":** no hay chat en vivo ni comentarios, y los recordatorios de próximas
  transmisiones no se envían [F29]. **Ventaja para menores:** el chat queda en nuestra app, con miembros
  identificados, sin mensajes privados y auditado.

## 4. Idea que no estaba en el doc anterior: servidor propio "bajo demanda"

Hetzner Cloud cobra **por hora** con tope mensual. Desde el 15-jun-2026 [F20]:

| Servidor | Por hora | Por mes |
|---|---|---|
| CCX23 | €0,1378 | €85,99 |
| CCX33 | €0,2219 | €138,49 |

Si el backend crea la VM ~15 min antes de la clase desde un snapshot con Jitsi, BigBlueButton o LiveKit
ya instalado, y la destruye al terminar, 9–17 clases de ~1,5 h cuestan **€3–6/mes de cómputo** en lugar de
€86–139.

Lo que hay que construir y cuidar:
- Llamadas a la API de Hetzner y DNS en Cloudflare, o una IP fija reservada.
- El certificado TLS dentro del snapshot.
- Copiar la grabación a R2 antes de destruir la VM.
- Un plan B si Hetzner no tiene stock de CCX en ese momento.

**No verificado:** costo mensual de la IP fija y del almacenamiento de snapshots tras el ajuste de junio.
Hetzner no tiene centros en Latinoamérica (doc anterior). Es la vía más barata para tener video **embebido
y propio**, pero solo si hay alguien técnico que la mantenga.

## 5. Ranking recomendado (top 3)

1. **Google Meet con la cuenta Workspace de la organización, o Workspace for Nonprofits.** US$0; US$3,50
   por instructor si se quiere grabar.
   - **A favor:** familiar para todos, sala de espera, 100–150 personas, API para crear salas y leer
     asistencia.
   - **En contra:** no se embebe (se abre aparte) y depende de que la organización sea elegible.
2. **JaaS (Jitsi de 8x8).** ≈ US$99–109/mes.
   - **A favor:** única opción SaaS que se ve *dentro* de nuestra app. Se integra con nuestro login (JWT
     desde FastAPI), sin servidores, con lobby y sin chat privado. Se puede pilotar gratis con el plan
     Developer (25 MAU).
   - **En contra:** el precio sube si crecen los usuarios únicos, y la grabación dura solo 24 h en 8x8.
3. **YouTube Live con chat propio.** US$0.
   - **A favor:** ideal para la clase magistral de 100.
   - **En contra:** una sola vía; se combina con 1 o 2 para preguntas y grupos chicos.

Fuera del top 3, pero a tener en cuenta:
- **Zoho Meeting Standard** (US$6–7, con grabación): el plan B pago más barato.
- **Jitsi o BigBlueButton bajo demanda:** si hay alguien técnico.
- **LiveKit** (doc anterior): sigue siendo el camino estratégico si más adelante hay presupuesto y se
  quiere una UI 100 % propia.

## 6. Plan mínimo (US$0/mes) y qué hay que construir

**Operación:**
- **Clase de 100:**
  - El instructor transmite en YouTube Live como **no listado**, con inserción permitida y la audiencia
    "hecho para niños" si aplica.
  - La app la muestra embebida (`youtube-nocookie.com/embed/{id}`) solo a los inscritos.
  - Al lado, **chat propio** moderado (FastAPI): solo miembros, sin mensajes privados, guardado para
    auditoría.
- **Interacción** (dudas, exámenes orales, grupos chicos): enlace de Google Meet creado con la cuenta de
  la organización. Acceso restringido, el instructor admite a cada uno, y regla de dos adultos.
- **Si la organización no es elegible:**
  - Gmail gratis: sirve si la sesión dura menos de 60 min.
  - Zoho Meeting Standard 100: **US$6–7/mes**, graba.
- **Cuando haga falta interacción embebida:** subir a JaaS Basic (US$99) sin cambiar el modelo de datos.

**Qué construir (≈ 6–9 días de desarrollo):**

Campos en la tabla `live_sessions` propuesta en el doc anterior, a la que apuntan la sesión de curso y la
de examen:

| Campo | Para qué |
|---|---|
| `provider` | youtube, google_meet, jaas, zoom, teams, zoho, webex, bbb, livekit u other |
| `mode` | broadcast (transmisión) o interactive (interactiva) |
| `join_url` | Validada contra una lista de dominios permitidos por proveedor, para que nadie pegue enlaces arbitrarios |
| `embed_url` | Nullable; solo para youtube, jaas o bbb |
| `provider_meeting_id` | ID del video, espacio de Meet o sala de JaaS |
| `host_url` / `stream_key` | Solo para el instructor, cifrado, nunca se envía a alumnos |
| `passcode` | Opcional |
| `starts_at`, `ends_at`, `join_opens_minutes_before` | Horario y ventana de acceso |
| `status` | Estado de la sesión |
| `chat_mode` | app, provider u off |
| `made_for_kids` | Audiencia declarada |
| `recording_enabled`, `recording_consent_required` | Grabación y si exige consentimiento |
| `recording_url` / `recording_r2_key`, `recording_expires_at` | Dónde queda la grabación y hasta cuándo |
| `second_adult_id` | Regla de dos adultos |

**Endpoints:**
- Crear y editar la sesión (instructor).
- `GET /live-sessions/{id}/join`: devuelve el enlace **solo** si la persona está inscrita, dentro de la
  ventana y con el consentimiento parental vigente; deja registro.
- Chat moderado (envío, lista y borrado por el moderador).
- Asistencia (botón "Estoy presente"; más adelante, la API de Meet o los webhooks de JaaS).

**Frontend:**
- Página `/cursos/[id]/en-vivo` con el iframe y cuenta regresiva.
- Botón "Abrir en Meet" (`rel="noopener"`).
- CSP con `frame-src` limitado a `youtube-nocookie.com` (y `8x8.vc` cuando llegue JaaS).

## 7. Lo que NO se pudo verificar

- Precios oficiales de Webex Meet y Suite (solo terceros).
- Precio de Blindside Networks y bbbserver.
- Si el plan Developer de JaaS permite exceso pagado.
- Términos sobre menores de JaaS, Webex y Zoho.
- Si los 10.000 min del AV SDK de Zoho son mensuales o por única vez.
- Costo de la IP fija y los snapshots de Hetzner tras jun-2026.
- Elegibilidad de iglesias en Microsoft for Nonprofits.
- Límite exacto de Meet en el plan para ONG (100 o 150).
- Precio de Microsoft 365 Business Basic en la página oficial (se usó SWK, que cita el anuncio de
  Microsoft).
- Latencia en Sudamérica de todos los proveedores. **Medir en un piloto antes de decidir.**

## Fuentes

- [F1] JaaS precios — https://cpaas.8x8.com/en/pricing/jitsi-as-a-service-pricing/
- [F2] JaaS FAQ (MAU por dispositivo, 500 por reunión, grabación 24 h) — https://developer.8x8.com/jaas/docs/faq/
- [F3] JaaS iframe API — https://developer.8x8.com/jaas/docs/iframe-api-integration/ · https://developer.8x8.com/jaas/docs/iframe-api-overview/
- [F4] Requisitos de Jitsi — https://jitsi.github.io/handbook/docs/devops-guide/devops-guide-requirements/
- [F5] Jitsi IFrame API — https://jitsi.github.io/handbook/docs/dev-guide/dev-guide-iframe/
- [F6] Instalación de BigBlueButton — https://docs.bigbluebutton.org/administration/install/
- [F7] API de BigBlueButton — https://docs.bigbluebutton.org/development/api/
- [F8] MynaParrot, precios de BBB — https://www.mynaparrot.com/bigbluebutton-hosting-pricing · [F8b] https://blindsidenetworks.com/hosting/ · [F8c] https://bbbserver.com/pricing · [F8d] https://higheredlab.com/bigbluebutton-cost/
- [F9] Google Workspace para ONG — https://www.google.com/nonprofits/offerings/workspace/ · [F9b] https://workspace.google.com/pricing
- [F10] Elegibilidad de Google for Nonprofits — https://support.google.com/nonprofits/answer/3215869?hl=en · https://get.steeplemate.com/2025/10/01/how-google-for-nonprofits-works-and-how-your-church-can-qualify/
- [F11] Meet REST API — https://developers.google.com/workspace/meet/api/guides/overview · [F11b] https://developers.google.com/workspace/meet/api/guides/quickstart/python
- [F12] Límites de Meet gratis — https://support.google.com/meet/answer/7317473?hl=en · https://meetgeek.ai/blog/google-meet-time-limit
- [F13] Meet no se puede embeber — https://issuetracker.google.com/issues/289696532 · https://support.google.com/meet/thread/35165667
- [F14] SDK de complementos de Meet — https://developers.google.com/workspace/meet/add-ons/guides/overview
- [F15] Control de acceso en Meet — https://support.google.com/a/users/answer/11989526?hl=en
- [F16] Precios de Zoom — https://zoom.us/pricing
- [F17] Términos de Zoom (menores de 16) — https://www.zoom.com/en/trust/terms/
- [F18] Zoom Meeting SDK web — https://developers.zoom.us/docs/meeting-sdk/web/ · [F18b] https://developers.zoom.us/docs/meeting-sdk/
- [F19] Meeting SDK con cuenta gratis (foro de Zoom) — https://devforum.zoom.us/t/can-the-zoom-meeting-sdk-app-be-used-for-free-or-do-i-need-to-buy-a-developer-business-plan/137403
- [F20] Ajuste de precios de Hetzner (15-jun-2026) — https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/
- [F21] Precios de Nextcloud Enterprise — https://nextcloud.com/pricing/
- [F22] nextcloud-spreed-signaling — https://github.com/strukturag/nextcloud-spreed-signaling
- [F23] Escalabilidad de Nextcloud Talk — https://nextcloud-talk.readthedocs.io/en/latest/scalability/
- [F24] Element Call, auto-alojamiento — https://github.com/element-hq/element-call/blob/livekit/docs/self_hosting.md
- [F25] Zoom Video SDK — https://developers.zoom.us/blog/video-sdk-fact-sheet/ · https://www.zoom.com/en/video-sdk/ · https://trtc.io/blog/details/zoom-video-sdk-pricing-2026 (secundaria)
- [F26] Cuota de la API de YouTube — https://developers.google.com/youtube/v3/determine_quota_cost
- [F27] Empezar a transmitir en YouTube — https://support.google.com/youtube/answer/2474026
- [F28] Restricciones de transmisión y menores — https://support.google.com/youtube/answer/2853834?hl=en · https://support.restream.io/en/articles/8948773-enable-live-streaming-on-youtube
- [F29] Contenido "hecho para niños" — https://support.google.com/youtube/answer/9632097?hl=en
- [F30] Precio de Microsoft 365 desde jul-2026 — https://www.swktech.com/microsoft-365-price-increases-will-take-effect-july-2026/
- [F31] Cambios en licencias para ONG de Microsoft — https://www.pax8.com/blog/microsoft-365-licensing-changes-for-nonprofits/
- [F32] Elegibilidad de Microsoft Nonprofits — https://www.microsoft.com/en-us/nonprofits/eligibility
- [F33] Precios de interoperabilidad ACS–Teams — https://learn.microsoft.com/en-us/azure/communication-services/concepts/pricing/teams-interop-pricing
- [F34] Límites de Teams (secundaria) — https://learn.microsoft.com/en-us/answers/questions/5828670/teams-attendance-limit
- [F35] Interoperabilidad de Teams — https://learn.microsoft.com/en-us/azure/communication-services/concepts/teams-interop · [F35b] https://learn.microsoft.com/en-us/graph/api/application-post-onlinemeetings?view=graph-rest-1.0
- [F36] Anónimos y lobby en Teams — https://learn.microsoft.com/en-us/microsoftteams/anonymous-users-in-meetings · https://learn.microsoft.com/en-us/microsoftteams/who-can-bypass-meeting-lobby
- [F37] Precios de Webex — https://pricing.webex.com/us/en/
- [F38] Webex Meet (terceros) — https://comparedge.com/tools/webex/pricing · https://www.stackscored.com/pricing/video-conferencing/webex/
- [F39] Webex Guest Issuer — https://developer.webex.com/docs/guest-issuer · [F39b] https://developer.webex.com/explore/docs/frequently-asked-questions
- [F40] Webex Meetings Widget y Browser SDK — https://developer.webex.com/blog/the-new-webex-meetings-widget · https://developer.webex.com/docs/meetings
- [F41] Precios de Zoho Meeting — https://www.zoho.com/meeting/pricing.html (valores de https://www.zoho.com/sites/zweb/json/pricing/meeting-pricing-val.json)
- [F42] Configurar el AV SDK de Zoho — https://help.zoho.com/portal/en/kb/meeting/avsdk/articles/how-to-setup-avsdk
- [F43] Zoho Video SDK — https://www.zoho.com/meeting/videosdk.html
- [F44] Widget de embebido de Zoho — https://help.zoho.com/portal/en/kb/meeting/user-guide/meetings/embed-a-meeting-link/articles/embed-a-meeting-link
- Licencias y versiones de nextcloud-spreed-signaling, element-call (v0.26.0), bigbluebutton (v3.0.37),
  jitsi-meet y jibri: API de GitHub, consultada el 22-sep-2026.
