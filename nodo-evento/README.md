# Nodo local del evento — el camporee sin internet

En La Morita (N.L., 20–22 nov 2026) probablemente **no habrá internet**. La solución: todos los
teléfonos se conectan a **un mismo Wi-Fi del evento**, y un **mini PC** en esa red corre la
plataforma completa (base de datos, API, sitio web) con los **mismos nombres** que en internet:
`https://events.adventist.club`. El router responde que ese nombre es el mini PC, y el mini PC
tiene un certificado HTTPS real. Para el teléfono es «el sitio de siempre»: cámara para leer pases,
caché sin señal y cola de envíos funcionan igual.

```
 Teléfonos (jueces, coordinación, directores)
        │  Wi-Fi «Camporee» (WPA2, VLAN 30, sin IPv6)
        ▼
 AP exteriores TP-Link EAP610-Outdoor ──PoE 802.3at── Switch PoE ──┐
                                                                   │ Cat6
                                     MikroTik hAP ax³ (gateway) ◄──┘
                                     DHCP · DNS estático · firewall
                                                │ Ethernet
                                                ▼
                         Mini PC Linux  10.20.40.10  (Docker Compose)
                         Caddy :443 → Next (web) → FastAPI (api) → PostgreSQL
                         respaldo cada 10 min → USB
 (opcional) GL.iNet Beryl AX ─► WAN del MikroTik: internet de otro Wi-Fi, sólo para export/import
```

Antes del evento se **exporta** el evento de producción al nodo y producción queda **bloqueada**
para ese evento («en nodo local»: la nube sólo deja consultar, nadie captura en dos lugares).
Durante el evento el nodo es la fuente de verdad. Al volver internet se **importa** todo a
producción y el bloqueo se libera.

> **Versión «una Mac + GL.iNet Beryl AX»** (sin mini PC ni MikroTik): app de un clic
> «Nodo Camporee» en [`mac/README.md`](mac/README.md). Usa este mismo `docker-compose.yml`.
>
> En la app de Mac:
> - **Modo automático (recomendado):** «Conectar con adventist.club» con la cuenta de coordinación →
>   Mis eventos → qué actividades captura ese nodo → Preparar. Sin secretos que teclear
>   ([mac/README.md §3.1](mac/README.md)). Subir **avance** o **final** igual (§6.1).
> - **Modo manual (respaldo, sin la API):** conexión de producción, como en este README (§3.2/§6.2).
> - **Varios coordinadores / varios nodos:** cada uno con su Mac, su router y sus actividades (§6A).
> - **Red del evento:** GL.iNet, **Red pública del evento** (sólo el sitio, sin desvíos), **Google
>   Wifi / Nest Wifi** y otros routers con DNS personalizado (DNS en la Mac), red del lugar (§6B, §6C).

---

## 1. Equipo

Tomado del prototipo de red del propietario («Stack y equipo» / «Red local sin internet»).

| # | Equipo | Para qué | Notas |
|---|---|---|---|
| 1 | **MikroTik hAP ax³** | Gateway: DHCP, DNS estático, firewall, VLAN del evento | RouterOS v7. Recomendación para una primera instalación; dimensionar usuarios antes de escalar |
| 2 | **TP-Link EAP610-Outdoor** (1 por zona exterior) | Wi-Fi 6 para los teléfonos | Alimentación PoE 802.3at; conectar por Ethernet, no en malla, para estabilidad. Cantidad según medición en sitio |
| 3 | **Switch PoE 802.3at + cable Cat6 exterior** | Distribuye red y energía a los AP | Verificar presupuesto de potencia del switch. **El PoE pasivo del hAP ax³ no sustituye un switch o inyector 802.3at para el EAP610** |
| 4 | **Mini PC** con Linux | Corre todo el sistema | Sugerido: x86-64, 4+ núcleos, **16 GB RAM**, SSD 256 GB+, Ethernet gigabit, BIOS «encender al volver la corriente». Ubuntu Server 24.04 LTS |
| 5 | **UPS / no-break** (≥ 600 VA) | Mini PC + MikroTik + switch | Un apagón no debe tirar la base a medio escribir |
| 6 | **Memoria USB o disco externo** (≥ 32 GB) | Respaldos automáticos | Y una **segunda** USB para copiar los respaldos cada noche |
| 7 | GL.iNet Beryl AX · *opcional* | Entrada de internet desde otro Wi-Fi (hotspot, hotel) | Su LAN alimenta la WAN del MikroTik. Sólo hace falta para export/import; el evento no la necesita |
| 8 | Par TP-Link CPE510 · *sólo sin cable* | Enlace direccional entre dos puntos con línea de vista | En el extremo remoto, un AP atiende los teléfonos. No es antena de cobertura |
| 9 | Laptop del operador | Terminal del mini PC (SSH por cable) y monitoreo | Con la CA local instalada por si hace falta el plan B |
| 10 | Pases impresos de todos los clubes + extensiones, regletas, cinchos | | Los pases se imprimen ANTES del export (§6) |

Router y módem cumplen funciones distintas: si en algún momento hay internet de un proveedor, se
conserva su módem y el MikroTik se conecta detrás.

## 2. Cableado

1. Mini PC → puerto **ether2** del MikroTik (Ethernet, nunca Wi-Fi). IP fija **10.20.40.10**
   (o reserva DHCP por MAC).
2. Switch PoE 802.3at → puerto **ether3** del MikroTik (troncal con la VLAN 30).
3. Cada EAP610-Outdoor → un puerto PoE del switch (Cat6 exterior, conectores protegidos).
4. (Opcional) Beryl AX LAN → **ether1 (WAN)** del MikroTik.
5. Mini PC, MikroTik y switch → UPS. AP exteriores: PoE desde el switch (también en la UPS).

## 3. Red y DNS en el MikroTik

Red del evento: `10.20.40.0/24`, gateway `10.20.40.1`, nodo `10.20.40.10`, DHCP `10.20.40.50–250`,
SSID «Camporee» con contraseña WPA2, **clientes aislados entre sí**, **sin IPv6** en esta VLAN
(el prototipo: «HotSpot inicialmente en IPv4 y cerrar bypass de IPv6»).

**Lo esencial — DNS estático** (RouterOS v7, Terminal de WinBox o SSH):

```routeros
/ip dns set allow-remote-requests=yes cache-size=4096KiB
/ip dns static add name=events.adventist.club address=10.20.40.10 ttl=5m comment="nodo evento"
/ip dns static add name=admin.adventist.club  address=10.20.40.10 ttl=5m comment="nodo evento"
/ip dns static add name=api.adventist.club    address=10.20.40.10 ttl=5m comment="nodo evento"
# Que el DHCP entregue el router como DNS
/ip dhcp-server network set [find address="10.20.40.0/24"] dns-server=10.20.40.1 gateway=10.20.40.1
```

Para que ningún teléfono se salte el DNS del router (DNS fijo 8.8.8.8, «DNS privado»):

```routeros
# Toda consulta DNS de la VLAN del evento va al router, aunque el teléfono pida otro servidor
/ip firewall nat add chain=dstnat in-interface=vlan30 protocol=udp dst-port=53 action=redirect to-ports=53 comment="DNS al router"
/ip firewall nat add chain=dstnat in-interface=vlan30 protocol=tcp dst-port=53 action=redirect to-ports=53 comment="DNS al router"
# DNS cifrado (DoT): rechazar para que Android caiga al DNS normal
/ip firewall filter add chain=forward in-interface=vlan30 protocol=tcp dst-port=853 action=reject reject-with=tcp-reset comment="sin DoT"
/ip firewall filter add chain=forward in-interface=vlan30 protocol=udp dst-port=853 action=reject comment="sin DoT"
```

Firewall de la VLAN del evento (como en el prototipo): permitir DNS/DHCP del gateway y
HTTP/HTTPS (80/443) hacia `10.20.40.10`; bloquear el resto (incluidas otras redes y el puerto
de la base de datos, que de todos modos no se publica fuera de Docker). Si hay internet por el
Beryl, puede dejarse salir a los teléfonos o no: el sitio del evento ya resuelve al nodo.

**Comprobar** desde un teléfono o la laptop conectados al Wi-Fi del evento:
`nslookup events.adventist.club` → `10.20.40.10`, y abrir `https://events.adventist.club/nodo-salud` → `ok`.

Si un teléfono tiene **«DNS privado» en modo estricto** (Android: Ajustes › Redes › DNS privado
con un nombre) o una **VPN** activa, no verá el nodo: pedirle «Automático/Desactivado» o apagar la VPN.

## 4. Secretos

| Secreto | Dónde | Regla |
|---|---|---|
| `JWT_SECRET` | `.env` del nodo | **Propio del nodo** (lo genera `./nodo.sh init`). Las sesiones de producción no valen en el nodo: cada juez inicia sesión **una vez** en el nodo con **su misma contraseña** (los hashes viajan en el export). Un nodo robado no sirve para firmar sesiones de producción |
| `EVENT_PASS_SECRET` | `.env` del nodo | **Igual al de producción**: con él se leen los pases guardados. Si no coincide, al mostrarse un pase en el nodo se regeneraría y el QR impreso dejaría de servir. El export lo verifica («pases en el nodo: N legibles, 0 ilegibles») y se detiene si no coincide |
| `PROXY_SHARED_SECRET` | `.env` del nodo | **Propio del nodo** (lo genera `init`); lo usan la API y Next del nodo para que los límites de inicio de sesión cuenten la IP real de cada teléfono (`docs/RATE_LIMITS.md`). No es el de producción |
| `POSTGRES_PASSWORD` | `.env` del nodo | La genera `init`; la base sólo existe en la red interna de Docker |
| `PROD_DATABASE_URL` | **en ningún archivo** | Se teclea cuando `./nodo.sh export/import` la pide (no se muestra ni queda en el historial) |
| `CF_DNS_API_TOKEN` | **en ningún archivo** | Sólo al sacar el certificado (§5) |

**Cómo pasar `EVENT_PASS_SECRET` al nodo sin exponerlo:** el propietario lo copia del panel de
Render (servicio `adventist-club-api` › Environment) **directamente en la terminal del mini PC**
cuando `./nodo.sh init` lo pide (no se ve al teclear), o lo comparte por el gestor de contraseñas.
**Nunca** por chat, correo, WhatsApp, captura de pantalla ni en un archivo del repositorio.

> **Importante (decisión del propietario):** si en Render `EVENT_PASS_SECRET` está **vacío**, la
> plataforma cifra los pases con `JWT_SECRET` de producción, y el nodo necesitaría ese valor
> (quien lo tenga puede firmar sesiones de producción). Recomendado: **antes de imprimir los
> pases**, fijar en Render un `EVENT_PASS_SECRET` nuevo y exclusivo (p. ej. `openssl rand -hex 32`).
> Cada pase ya guardado se regenera una sola vez al mostrarse; hoy los pases del Universo aún no
> se han mostrado ni impreso, así que no se pierde nada. Después, ese es el valor que va al nodo.

`.env` queda con permisos 600. Recomendado: disco del mini PC cifrado (LUKS al instalar Ubuntu):
contiene hashes de contraseñas y el secreto de los pases.

## 5. Certificado HTTPS

**Plan A — certificado real de Let's Encrypt (DNS-01 en Cloudflare), 1–3 semanas antes**, en
cualquier máquina con Docker e internet (el mini PC mismo):

```sh
read -rs CF_DNS_API_TOKEN && export CF_DNS_API_TOKEN   # token Cloudflare: Zone › DNS › Edit, sólo adventist.club
export LE_EMAIL=correo@del-propietario
./tls/certificado-letsencrypt.sh --staging               # ensayo (certificado no confiable)
./tls/certificado-letsencrypt.sh                         # el real: events + admin + api.adventist.club
```

No cambia nada en el DNS público (sólo crea y borra un registro TXT temporal). Deja
`tls/actual/fullchain.pem` y `privkey.pem` (600). Vence a los ~90 días: con sacarlo en noviembre
alcanza; `--renovar` si faltan menos de 30 días. Después borra el token de Cloudflare o revócalo.

**Plan B — CA local** (sin certificado público): `./tls/ca-local.sh` (opcionalmente con la IP del
nodo: `./tls/ca-local.sh events.adventist.club admin.adventist.club api.adventist.club 10.20.40.10`).
Cada teléfono debe instalar y confiar en `tls/actual/ca-local.pem` (iPhone: perfil + «Ajustes de
confianza de certificados»; Android: «Instalar certificado de CA»). Mucha fricción con 100
teléfonos y, si el teléfono ya visitó `events.adventist.club` en internet, el navegador puede
negarse (HSTS). Sirve para pruebas y como último recurso.

## 6. Instalación del mini PC (una vez, con internet)

```sh
# Ubuntu Server 24.04 LTS, usuario «operador», IP fija 10.20.40.10 en la red del evento
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git openssl curl
sudo usermod -aG docker "$USER" && newgrp docker
sudo systemctl enable docker          # arranca solo al encender
mkdir -p ~/nodo && cd ~/nodo
git clone https://github.com/michstudio-cloud/adventist.club.git backend-repo
git clone https://github.com/michstudio-cloud/conquistadores.app.git frontend
cd backend-repo/nodo-evento
./nodo.sh init                        # .env: genera secretos del nodo y pide EVENT_PASS_SECRET
# En .env: FRONTEND_DIR=../../frontend, RESPALDO_DIR=/media/respaldo-nodo (USB montada),
#          NODO_IP=10.20.40.10, WEB_BUILD_ID=<git -C ../../frontend rev-parse HEAD>
./tls/certificado-letsencrypt.sh      # §5
./nodo.sh construir                   # construye las imágenes (descarga lo necesario)
./nodo.sh arrancar                    # y comprueba https://events.adventist.club en el nodo
```

`construir` es lo ÚLTIMO que necesita internet además de export/import. Usa el **mismo commit**
del frontend y del backend que está en producción (`git checkout <commit>` en ambos clones).

## 7. Operación diaria

| Qué | Cómo |
|---|---|
| Encender | Se levanta solo al encender el mini PC (Docker + `restart: unless-stopped`). A mano: `./nodo.sh arrancar` |
| Apagar | `./nodo.sh apagar` (los datos se quedan en `datos/`). Luego `sudo poweroff` |
| ¿Está bien? | `./nodo.sh estado`: servicios, HTTPS (web + API) y último respaldo |
| Registros | `./nodo.sh logs api` (o web, caddy, db, respaldo) |
| Reiniciar algo | `./nodo.sh reiniciar api` · sin nombre: todo |

**Sin internet en tiempo de ejecución:** la API, la web, la base y los respaldos están en una
red de Docker **sin salida** (`internal: true`): no pueden depender de nada externo aunque el nodo
tenga internet. Correo apagado (sin Resend: el sistema no pide códigos por correo para entrar),
mapas apagados, sin R2 (subir logos no está disponible en el nodo; el logo del evento se copia en
el export), sin Sentry. Las fuentes y scripts van dentro de la imagen web.

## 8. Respaldos

- **Automáticos:** `pg_dump` cada `RESPALDO_CADA_MIN` minutos (10 por defecto) a `RESPALDO_DIR`
  (la USB). Se escribe a un temporal, se valida con `pg_restore -l` y se renombra; se conservan
  los últimos `RESPALDO_CONSERVAR` (200 ≈ 33 h) y `ultimo.dump` es siempre el más reciente.
- **Manual:** `./nodo.sh respaldar` (antes de algo delicado: cerrar el evento, reabrirlo…).
- **Cada noche:** copiar la carpeta de respaldos a la segunda USB y guardarla aparte.
- **Restaurar:** `./nodo.sh restaurar nodo-20261121-1840.dump` (pide escribir RESTAURAR; antes
  guarda un respaldo de lo actual).
- Si la USB se desconecta, `./nodo.sh logs respaldo` muestra «no se puede escribir».

## 9. Guion día por día

**T−21 a T−7 (con internet)**
1. Decidir y fijar `EVENT_PASS_SECRET` en Render (§4) **antes de imprimir pases**.
2. Certificado Let's Encrypt (§5). Instalar y construir el mini PC (§6).
3. Ensayo completo con el equipo real: router, AP, DNS, 3–4 teléfonos (iPhone y Android),
   login, leer un pase, capturar, apagar el router de internet, seguir capturando.

**Víspera (con internet)**
1. Coordinación imprime los pases de todos los clubes (Clubes › Imprimir pases) en producción.
2. Últimos cambios de reglas, personal e inscripciones **en producción**.
3. Export (el mini PC con internet, p. ej. por el Beryl):
   ```sh
   ./nodo.sh export --event <id del evento> --actor-email <correo de coordinación> \
                    --node-label "Mini PC La Morita" --dry-run      # ver qué se copiaría
   ./nodo.sh export --event <id> --actor-email <correo> --node-label "Mini PC La Morita"
   ```
   Copia ministerios, árbol de organizaciones, las cuentas que participan (personal, directores
   de los clubes inscritos, administración de la asociación hacia arriba; nunca menores) con sus
   hashes de contraseña, sus roles y membresías, y el evento con todas sus filas. Marca el evento
   en producción **«en nodo local»**: desde ese momento la nube responde 423 a toda escritura del
   evento («se está operando sin internet en el nodo local…») y sólo deja consultar. Verifica que
   los pases se leen con el `EVENT_PASS_SECRET` del nodo.
4. Probar en el nodo: un juez inicia sesión, abre el evento, lee un pase impreso.

**Días del evento (sin internet)**
1. Encender UPS → MikroTik → switch/AP → mini PC. `./nodo.sh estado`.
2. Jueces y coordinación se conectan al Wi-Fi «Camporee», abren `events.adventist.club` e
   inician sesión (una vez) con su contraseña de siempre.
3. Llegadas (check-in), capturas, ajustes, todo normal. Cada noche: `./nodo.sh respaldar` y copia
   de los respaldos a la segunda USB.

**Al volver internet**
1. Último respaldo: `./nodo.sh respaldar`.
2. Simulación, revisar el reporte:
   ```sh
   ./nodo.sh import --event <id> --actor-email <correo> --dry-run --report /exports/import-dry.json
   ```
3. Aplicar: `./nodo.sh import --event <id> --actor-email <correo>`. En una sola transacción sube
   llegadas, evaluaciones y revisiones, ajustes, cambios de reglas y estado, inscripciones y
   personal; copia la bitácora del nodo; guarda el reporte en producción y **libera el bloqueo**.
   Es idempotente: repetirlo no duplica nada (la segunda vez dice «sin cambios»).
4. Si hay **conflictos** (algo cambió en la nube Y en el nodo): no se aplican esas filas, el
   bloqueo sigue, y el reporte dice cuáles. Decidir y repetir con `--prefer node` o `--prefer prod`.
5. Revisar en producción el panel del evento. Guardar la USB de respaldos hasta el cierre oficial.

Recomendado antes del import: una rama de respaldo de Neon (como en cada migración).

## 10. Si algo falla

| Síntoma | Qué hacer |
|---|---|
| Un teléfono dice «sin conexión» | Sus capturas quedan en su **cola local** («N por enviar») y se envían solas al volver la señal (las capturas tienen clave de idempotencia: nunca se duplican). Acercarse a un AP |
| Un teléfono capturó con datos móviles en la nube | La nube responde 423 y la captura **queda en la cola** (no se pierde); al volver al Wi-Fi del evento llega al nodo |
| El sitio no abre en ningún teléfono | `./nodo.sh estado`. Si HTTPS no responde: `./nodo.sh reiniciar caddy web api`. Probar DNS con `nslookup` (§3) |
| La API da error 500 o se cuelga | `./nodo.sh logs api`; `./nodo.sh reiniciar api`. Los teléfonos reintentan solos |
| La base se dañó | `./nodo.sh restaurar ultimo.dump`; las capturas posteriores al respaldo que sigan en las colas de los teléfonos se reenvían solas |
| El mini PC murió | Otro equipo con Docker (la laptop): copiar `nodo-evento/` con su `.env` y `tls/`, `./nodo.sh construir` (si hay internet) o cargar imágenes guardadas (`docker save`/`docker load`), `./nodo.sh arrancar`, `./nodo.sh restaurar ultimo.dump`, y cambiar la IP del DNS estático del MikroTik a la nueva máquina |
| Apagón | La UPS da margen para `./nodo.sh apagar`. Al volver la luz, todo arranca solo |
| Certificado rechazado | ¿Venció? `openssl x509 -enddate -noout -in tls/actual/fullchain.pem`. Último recurso: plan B (CA local) |
| Todo lo anterior falló | Hojas de captura en papel por actividad; se capturan en el nodo o en producción después |
| Hay que liberar producción sin importar | `DELETE /api/v1/events/{id}/offline-lock` con motivo (coordinación). Lo que esté en el nodo habrá que importarlo después; el import marcará conflictos si la nube cambió |

## 11. Qué cambia en la plataforma (para desarrolladores)

- **Migración 029** (`backend/migrations/029_event_offline_lock.sql`): columnas
  `events.offline_locked_at/_by_id/_lock_reason/offline_node_label` y tabla `event_offline_syncs`
  (cada export/import con las huellas md5 por fila al exportar y el reporte del import).
- **API:** `POST /api/v1/events/{id}/offline-lock` `{reason, node_label?}` y
  `DELETE …/offline-lock` (motivo en el cuerpo o `?reason=`), sólo coordinación, auditados
  (`EVENT_OFFLINE_LOCK` / `EVENT_OFFLINE_UNLOCK`). Con el bloqueo, toda escritura del evento
  responde **423** con el mensaje; lecturas, `resolve-pass`, `/pass/resolve` y `duplicate` siguen.
  `EventOut.offline_lock` = `{locked_at, locked_by_name, reason, node_label}` o `null`.
- **Web:** la cola de envíos trata 423 como «reintentar» (no como rechazo).
- **Base desde cero:** `backend/scripts/db_bootstrap.sh` (ver el README del backend).
- **Sincronización:** `backend/scripts/nodo_sync.py` (`export` / `import` / `status`).
- `DATABASE_URL` con `?sslmode=disable` desactiva TLS hacia la base (sólo el nodo: Postgres en la
  red interna de Docker). Cualquier otro host remoto sigue exigiendo TLS.

## 12. Prueba hecha en una Mac (14 oct 2026)

Con datos locales de prueba (nunca producción): «producción» = copia local `prod_sim` de la base
de auditoría `eventos_dev` + 029; evento «Camporee juez rápido» (IN_PROGRESS, 6 clubes).

- **Docker:** colima (Docker Engine 29 en una VM de 4 CPU / 6 GB) con este mismo `docker-compose.yml`.
  `./nodo.sh construir` + `arrancar`: base creada desde cero por `db_bootstrap.sh` (41 archivos).
- **Certificado:** plan B (`tls/ca-local.sh`) con `events/admin/api.adventist.club` y la IP de la Mac.
- **Export** (`./nodo.sh export`): 3 ministerios, 15 organizaciones, 11 cuentas, 9 roles,
  5 membresías y el evento completo (12 actividades, 10 tipos de ajuste, 4 personal, 6
  inscripciones, 5 llegadas, 31 evaluaciones, 4 revisiones); «producción» quedó bloqueada; «6
  pases legibles, 0 ilegibles». Una API local sobre `prod_sim` respondió **423** al check-in con el
  mensaje del nodo, y `offline_lock` en `GET /events/{id}`.
- **Otro dispositivo:** un Brave sin cabeza, con perfil propio y un contexto por persona, que
  sólo resuelve `events.adventist.club` → **IP LAN de la Mac** (`192.168.86.42`) y **ningún otro
  host** (sin internet), confiando en el certificado del nodo: contexto seguro ✔, service worker
  registrado ✔, 0 peticiones a otros dominios, 0 errores. Inicio de sesión de coordinación y de
  un juez (contraseña de siempre, hash copiado) ✔; check-in leyendo el QR (otorgó «Llegada al
  campamento 100») ✔ y repetido = idempotente ✔; escaneo on_scan ✔; 3 capturas de rúbrica ✔ y
  una reenviada con la misma clave = sin duplicar ✔; corrección con motivo ✔; bonificación de
  coordinación ✔; cambio de regla ✔. Capturas en `_dev-eventos/shots-nodo/`.
- **Sin internet:** la API y la web corren en una red de Docker sin salida: desde dentro, internet
  «no llega» (ENETUNREACH / sin DNS) y `api.adventist.club` resuelve al propio nodo. Todo lo
  anterior se hizo así. (Esta red sin salida destapó una dependencia real: `next start` intentaba
  instalar TypeScript de internet al arrancar; corregido en `web.Dockerfile`.) No se apagó el Wi-Fi
  de la Mac: habría cortado también la red local de la prueba.
- **Respaldo:** cada 2 min en la prueba (`RESPALDO_CADA_MIN=2`), validados con `pg_restore -l`;
  `./nodo.sh respaldar` y `./nodo.sh restaurar` probados.
- **Import:** simulación con reporte → 1 llegada, 4 evaluaciones, 1 revisión, 1 ajuste nuevos;
  1 actividad y 1 inscripción cambiadas; 8 filas de bitácora; un cambio hecho «a escondidas» en la
  nube a una evaluación que el nodo también corrigió = **CONFLICTO** (no se aplicó, su revisión
  esperó, el bloqueo siguió) y un cambio sólo de la nube = aviso (se conserva). `--prefer node`
  resolvió y **liberó el bloqueo**; un tercer import = «sin cambios» (idempotente). Después, la
  API sobre `prod_sim` aceptó escrituras de nuevo.
- Limitación de la prueba: no había un teléfono físico a mano; el «otro dispositivo» fue un
  navegador aparte que entra por la IP LAN. Falta el ensayo con teléfonos reales (iPhone y
  Android), el MikroTik y el certificado de Let's Encrypt (§9, T−21).

## 13. Decisiones del propietario

1. **Equipo:** comprar/confirmar mini PC (16 GB, SSD), UPS, 2 USB, MikroTik hAP ax³, switch PoE
   802.3at y cuántos EAP610-Outdoor (según medición en La Morita). ¿Beryl AX para tener internet
   en algún momento (export/import en sitio)?
2. **Certificado:** plan A (Let's Encrypt) requiere un token de Cloudflare con «Zone · DNS · Edit»
   para adventist.club, creado por el propietario. ¿Se incluye `admin.adventist.club`?
3. **`EVENT_PASS_SECRET`:** fijar uno exclusivo en Render antes de imprimir pases (recomendado) en
   vez de llevar al nodo el `JWT_SECRET` de producción (§4).
4. **Quién opera el nodo:** una persona responsable (y un suplente) con la laptop, que haga
   export la víspera, vigile `./nodo.sh estado`, copie respaldos cada noche y haga el import.
   Necesita la cadena de conexión de producción sólo en esos dos momentos.
5. **Respaldo de producción:** export e import escriben directo en Neon.
   Recomendado: rama de respaldo de Neon antes del import.
