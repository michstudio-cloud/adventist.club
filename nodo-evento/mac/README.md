# Nodo Camporee — la Mac que hace de servidor del evento

En el camporee (La Morita, 20–22 nov 2026) probablemente **no habrá internet**. Con esta app, una
Mac y un router de bolsillo **GL.iNet Beryl AX** se convierten en «el internet del evento»: los
teléfonos de jueces y coordinación se conectan al Wi-Fi del router, abren
**https://events.adventist.club** como siempre, y en realidad están hablando con la Mac.

```
 Teléfonos ──Wi-Fi──► GL.iNet Beryl AX ──cable──► Mac con «Nodo Camporee»
 (jueces,              192.168.8.1                 (base de datos, sitio, respaldos)
  coordinación)        «events.adventist.club = la Mac»
```

Si un teléfono se aleja y pierde la señal, **no pierde nada**: guarda sus capturas («N por
enviar») y las manda solo cuando vuelve a estar cerca del router.

Todo se maneja desde **una ventana** con botones grandes. No hace falta la Terminal (salvo la
instalación de la primera vez, que puede hacer alguien técnico).

---

## 1. Qué llevar

| Qué | Para qué |
|---|---|
| **Mac** (Apple M1 o más nueva, 16 GB recomendado) **y su cargador** | Es el servidor. Siempre conectada a la corriente. |
| **GL.iNet Beryl AX (GL-MT3000)** y su cable USB-C | El Wi-Fi del evento. |
| **Batería externa (power bank) USB-C de 20 000 mAh o más**, o una toma de corriente | Para el router (consume poco: una batería dura muchas horas). |
| **Adaptador USB-C a Ethernet + cable de red corto** (recomendado) | La Mac conectada al router por cable: más estable que por Wi-Fi. |
| **Memoria USB** (32 GB o más) con el nombre **`NODO-RESPALDO`** | Copia automática de los respaldos. Lleva una **segunda** para guardarla aparte cada noche. |
| Extensión eléctrica / regleta | Mac + router en el mismo punto. |
| Los **pases impresos** de todos los clubes | Se imprimen en producción antes de «Preparar evento». |

> **Nombrar la memoria USB:** conéctala, en el Finder haz clic derecho sobre ella › *Cambiar nombre*
> › escribe `NODO-RESPALDO` (mayúsculas, con guion). La app la detecta sola.

---

## 2. Preparación en casa (una sola vez, con internet)

### 2.1 Instalar (lo hace alguien técnico, 20–30 min)

```sh
xcode-select --install                       # herramientas de Apple (si no están)
brew install colima docker docker-compose    # Docker sin Docker Desktop (https://brew.sh)
mkdir -p ~/Nodo && cd ~/Nodo                # en la carpeta personal, NO en Documentos
git clone https://github.com/michstudio-cloud/adventist.club.git backend-repo
git clone https://github.com/michstudio-cloud/conquistadores.app.git frontend
./backend-repo/nodo-evento/mac/instalar.sh   # instala la app en Aplicaciones + Escritorio
```

Aparece **«Nodo Camporee»** en el Escritorio. Desde aquí, todo es con la app.

### 2.2 En la app

1. **Ajustes › Construir / actualizar** (10–15 min; descarga y prepara el sistema). Hacerlo de
   nuevo cada vez que se actualice la plataforma, antes del viaje.
2. **Ajustes › Secretos** — guardar (se piden una vez y quedan en el Llavero de la Mac):
   - **Secreto de los pases** (`EVENT_PASS_SECRET`): el MISMO de producción. Lo tiene el propietario
     en su gestor de contraseñas (también está en Render › adventist-club-api › Environment).
   - **Conexión de producción** (`postgresql://…`): sólo para «Preparar evento» y «Subir resultados».
   - **Token de Cloudflare**: sólo para el certificado.
   - **Contraseña del Wi-Fi del evento** (opcional): para el QR «Conéctate al Wi-Fi».
   - Los otros tres secretos (sesiones del nodo, web↔API, base) **los genera la app sola**.
3. **Certificado › Plan A (Let's Encrypt)**: escribe el correo para avisos y pulsa **Obtener**.
   Hazlo 1–3 semanas antes del evento (dura ~90 días; la tarjeta se pone ámbar si vence antes de
   que termine el evento o en menos de 30 días, y roja a 7 días).
4. **Configurar router** (con la Mac conectada al GL.iNet, ver §2.3).
5. **Ensayo**: Encender, conectar 2–3 teléfonos (iPhone y Android) al Wi-Fi del GL.iNet, abrir
   el sitio, iniciar sesión, leer un pase, capturar, apagar el Wi-Fi del teléfono, capturar, volver.

### 2.3 Configurar el router (una sola vez)

1. Enciende el GL.iNet (con su cable USB-C). La primera vez, en su panel (http://192.168.8.1)
   eliges idioma y **contraseña de administración**. Guárdala en el gestor de contraseñas.
2. Conecta la Mac al router: **cable** al puerto LAN del GL.iNet (recomendado) o a su Wi-Fi.
   - Si es por Wi-Fi: Ajustes del Sistema › Wi-Fi › (la red del GL.iNet) › **Detalles** ›
     *Dirección privada de Wi-Fi* → **Fija** (o desactivada). Si no, el router no la reconoce.
3. En la app, **Configurar router**: escribe la contraseña de administración (no se guarda), el
   nombre del Wi-Fi si quieres cambiarlo (p. ej. «Camporee NTAM»), y pulsa **1. Ver qué cambiará**.
   La app muestra exactamente lo que va a dejar:
   - La Mac siempre recibe la **misma IP** (p. ej. 192.168.8.150).
   - `events.adventist.club` y `api.adventist.club` (y `admin.adventist.club` si lo marcas) →
     **esa IP**, también sin internet.
   - Nada más (internet, contraseña del panel y otras reservas no se tocan).
4. **2. Aplicar**. Al final la app revisa sola el DNS; **Revisar** lo repite cuando quieras.

Repetirlo no duplica nada. Si la IP de la Mac cambia (otro adaptador, otro router), la tarjeta del
router se pone **roja** y ofrece volver a configurarlo.

**Si prefieres hacerlo a mano en el panel del GL.iNet** (o si la app no pudo):
- *Reserva de IP:* panel › **Network › LAN › Address Reservation** (según versión: «Reserva de
  direcciones»/«Static IP Address Binding») › añadir la Mac (aparece en la lista de clientes) con
  la IP que tiene ahora.
- *DNS:* panel › **System › Advanced Settings** › abrir **LuCI** › **Network › DHCP and DNS** ›
  pestaña *General* (o *DNS Records*/«Addresses», según versión) › en **Addresses** añadir
  `/events.adventist.club/192.168.8.X` y `/api.adventist.club/192.168.8.X` (X = la IP de la Mac; no hace falta `www`) ›
  *Save & Apply*.
- Si está activo **AdGuard Home** (Applications › AdGuard Home), apágalo: el DNS del evento tiene
  que pasar por el router.
- Comprobar desde la Mac: botón **Revisar**, o en la Terminal `dig @192.168.8.1 events.adventist.club`.

---

## 3. La víspera (con internet)

1. Coordinación imprime los pases en producción (Clubes › Imprimir pases).
2. Últimos cambios de reglas, personal e inscripciones **en producción**.
3. En la app: **Encender** y luego **Preparar evento**:
   - Evento: ya viene el del Universo (`10267b82-8260-4b27-ba41-de7924e3a9e5`).
   - Correo de coordinación: la cuenta que firma (p. ej. la del propietario).
   - **1. Simular** → revisa el reporte (cuántas cuentas, clubes, actividades… «pases legibles»).
   - **2. Copiar al nodo y bloquear la nube** → desde ese momento la nube sólo deja *consultar* ese
     evento; las capturas se hacen en el nodo.
4. Prueba: un juez entra en el nodo con su contraseña de siempre y lee un pase impreso.

---

## 4. Cada mañana del evento

1. Router: conéctalo a la batería/corriente (tarda ~1 min en dar Wi-Fi).
2. Mac: **conéctala al cargador**, al router (cable) y la USB `NODO-RESPALDO`.
3. Abre **Nodo Camporee** › **Encender** (si quedó encendida desde ayer, ya aparece verde).
4. Mira las luces: todo **verde** = listo. Pulsa **QR para teléfonos** y muéstralo a los jueces.

> Opcional: **Ajustes › Encender el nodo al abrir la Mac**. Así, si la Mac se reinicia, el nodo
> arranca solo al iniciar sesión.

## 5. Durante el evento

| Luz | Qué significa | Qué hacer |
|---|---|---|
| **Router GL.iNet** | La Mac ve el router y tiene su IP fija | Ámbar «Conecta el GL.iNet»: revisa cable/batería del router. No hay que tocar nada más: al volver, todo sigue. |
| **Sitio, API y base** | El sistema funciona | Ámbar/rojo varios minutos: **Ajustes › Registros** y avisa al técnico; prueba Apagar → Encender. |
| **Accesible en la red** | Los teléfonos pueden llegar a la Mac | Rojo: ver «Firewall» en §7. |
| **Certificado** | HTTPS válido | Debe estar verde desde casa. |
| **Respaldo** | Último respaldo (cada 10 min) y copia en la USB | Ámbar «sin USB»: conecta la `NODO-RESPALDO`. |
| **Teléfonos** | Cuántos teléfonos usaron el sitio en 10 min | Sólo informativo. |
| **Energía** | Cargador o batería | Ámbar/rojo «Con batería»: **conecta el cargador**. |
| **La Mac no se dormirá** | El nodo impide el reposo | **No cierres la tapa** (la pantalla sí puede apagarse). |

- Los respaldos quedan en tu **carpeta personal › Nodo Camporee › Respaldos** y se copian a la USB cada 10
  min. **Cada noche:** copia la carpeta `nodo-camporee` de la USB a la segunda memoria y guárdala aparte.
- Antes de algo delicado (cerrar el evento): tarjeta **Respaldo › Respaldar ahora**.
- Puedes cerrar la ventana: el nodo sigue encendido. Para apagar: abre la app › **Apagar** (hace un
  último respaldo primero).

## 6. Al terminar (cuando vuelva internet)

1. **Subir resultados** › **1. Revisar (simulación)**: el reporte dice qué se subirá (llegadas,
   evaluaciones, revisiones, ajustes…).
2. **2. Subir a producción** → hace un respaldo y sube todo en una sola operación, y libera la nube.
3. Si dice **conflictos** (algo se cambió en la nube Y en el nodo): elige **Gana el nodo** (lo que
   se capturó en el evento), **Gana la nube**, o **Dejar pendiente** y consultarlo.
4. Repetirlo no duplica nada (la segunda vez dice «sin cambios»).
5. **Apagar**. Guarda la USB de respaldos hasta el cierre oficial.

## 7. Si algo falla

| Problema | Solución |
|---|---|
| Un teléfono no abre el sitio | ¿Está en el Wi-Fi del GL.iNet? Si Android dice «sin internet», elegir **Mantener conexión** y apagar los **datos móviles**. Apagar VPN y «DNS privado» (Android: Ajustes › Redes › DNS privado › Automático/Desactivado). |
| El teléfono ya estaba dentro del sitio en internet | Volver a iniciar sesión: el nodo tiene sus propias sesiones (misma contraseña). |
| «Accesible en la red» en rojo | macOS bloquea la conexión. Ajustes del Sistema › Red › **Firewall** › Opciones… › busca **limactl** (o «ssh») y pon *Permitir conexiones entrantes*. Si al encender aparece «¿Permitir conexiones entrantes?», pulsa **Permitir**. |
| La IP de la Mac cambió (tarjeta roja) | **Configurar router** › Ver › Aplicar otra vez (pide la contraseña del router). |
| «Falta construir» | Necesita internet: Ajustes › Construir. Por eso se hace en casa. |
| El certificado vence o falla | En casa con internet: Certificado › Obtener. Último recurso sin internet: **Plan B (certificado local)**; cada iPhone debe abrir `http://<IP de la Mac>/nodo-ca.crt` (aparece como QR en «QR para teléfonos»), instalar el perfil y activarlo en Ajustes › General › Información › *Ajustes de confianza de certificados*. |
| La Mac pide permiso para «volumen extraíble», «Documentos» o conexiones entrantes | Pulsa **Permitir** (la app copia respaldos a la USB; al «Construir» lee el código si está en Documentos). |
| La Mac se apagó (batería) | Conéctala, ábrela, **Encender**. Los datos están en el disco; los teléfonos reenvían solos lo que tenían en cola. |
| La base se dañó | Técnico: `nodo-evento/nodo.sh restaurar ultimo.dump` con el `.env` del nodo, o pedir ayuda. Los respaldos están en la carpeta personal (Nodo Camporee › Respaldos) y en la USB. |
| Nada funciona | Hojas de captura en papel por actividad; se capturan después en el nodo o en producción. |

## 8. Qué tiene que dar el propietario

| Qué | Cuándo | Cómo |
|---|---|---|
| Contraseña de administración del **GL.iNet** | Al configurar el router (una vez) | La teclea en la app; **no se guarda**. |
| **Token de Cloudflare** (Zone · DNS · Edit, sólo `adventist.club`) | Al sacar/renovar el certificado | Ajustes › Secretos. Después puede revocarlo. |
| **EVENT_PASS_SECRET** de producción | Una vez | Ajustes › Secretos (desde su gestor de contraseñas). |
| **Conexión de producción** (`postgresql://…`) | Víspera y al volver | Ajustes › Secretos. Recomendado: rama de respaldo en Neon antes de «Subir resultados». |

Nunca por chat, correo, WhatsApp ni captura de pantalla.

---

## 9. Para técnicos

- **App:** SwiftUI (`app/*.swift`), compilada por `instalar.sh` con `swiftc` (sin proyecto de Xcode)
  en `~/Applications/Nodo Camporee.app`, alias en el Escritorio. La app sólo dibuja: todo lo hace
  el motor `nodo-mac.sh` (dentro del paquete, `Contents/Resources/`), que también se puede correr a
  mano: `nodo-mac.sh estado | jq`, `nodo-mac.sh router-aplicar --simular`, etc.
- **Docker:** Colima, perfil propio **`nodo`** (4 núcleos / 6 GB en Macs de 16 GB+, 40 GB de disco):
  no comparte disco ni contenedores con otros proyectos. El `docker-compose.yml` es el mismo del
  mini PC (`../README.md`); la app le pasa rutas en `~/Library/Application Support/Nodo Camporee/run/nodo.env`
  y los secretos sólo por el entorno.
- **Secretos:** Llavero de macOS (servicio `club.adventist.nodo-camporee`), siempre a través de
  `/usr/bin/security` por tuberías (no en argumentos ni en archivos). Así sobreviven a una
  reinstalación de la app sin preguntas. La contraseña del router sólo vive en memoria.
- **Configuración** (sin secretos): `~/Library/Application Support/Nodo Camporee/config.env`.
- **LaunchAgents** (`~/Library/LaunchAgents/club.adventist.nodo-camporee.*`): `despierto`
  (`caffeinate -ims` mientras el nodo está encendido), `respaldo-usb` (copia a la USB cada
  `RESPALDO_CADA_MIN`, corrida por el ejecutable de la app para que macOS le pida el permiso a la
  app), `autoarranque` (opcional, abre la app con `--encender` al iniciar sesión).
- **Carpeta de trabajo del nodo:** `instalar.sh` copia compose, Caddyfile y scripts a
  `~/Library/Application Support/Nodo Camporee/nodo/`; ahí viven también `datos/` (Postgres) y
  `tls/actual/`. Nada en Documentos/Escritorio: una pregunta de permisos de macOS sin contestar
  dejaría el estado o el arranque esperando. El repo sólo se usa para «Construir».
- **Respaldos:** el contenedor `respaldo` hace `pg_dump` cada 10 min a `~/Nodo Camporee/Respaldos`
  (validado con `pg_restore -l`); el espejo copia `nodo-*.dump` + `ultimo.dump` a
  `/Volumes/NODO-RESPALDO/nodo-camporee/` (sin borrar nada en la USB).
- **Router:** SSH `root@192.168.8.1` (contraseña = la del panel) con `uci`: sección
  `dhcp.nodo_camporee` (reserva) y `dhcp.@dnsmasq[0].address=/dominio/IP` (quita antes cualquier
  entrada previa del mismo dominio: idempotente); Wi-Fi opcional en los `wifi-iface` `ap`/`lan`.
  `--simular` muestra las órdenes sin conectarse.
- **Límite conocido:** con Colima todas las conexiones llegan a Caddy desde una IP interna, así
  que la API ve a todos los teléfonos con la misma IP (el límite por IP del nodo ya es 200/min y el
  límite por cuenta sigue igual). Por lo mismo, «equipos en la red» sale de la tabla ARP de la Mac.
- **Certificado plan B:** Caddy entrega la CA en `http://<ip>/nodo-ca.crt` (sólo si existe
  `tls/actual/ca-local.pem`; con Let's Encrypt responde 404).
- **CLI de la app:** `"Nodo Camporee" --motor <orden>` (con los secretos del Llavero),
  `--secreto-guardar NOMBRE` (valor por stdin), `--secreto-existe NOMBRE`, `--secreto-borrar NOMBRE`,
  `--secreto-prueba`, `--prueba` (abre la ventana con ganchos para pruebas automáticas:
  `swift pruebas/avisar.swift "hoja:qr"`, `"captura:/ruta/nombre"`, `encender`, `apagar`).
- **Pruebas sin router:** `pruebas/router-openwrt.sh` corre el guion del router dos veces en un
  OpenWrt real (imagen `openwrt/rootfs`, uci de verdad) y comprueba reserva, DNS, Wi-Fi, que no
  toca lo demás y que repetirlo no cambia nada.
