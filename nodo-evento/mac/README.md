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

> **¿Otra Mac (la de respaldo o la del operador)?** No hace falta nada de esto: usa el DMG
> portátil «Nodo Camporee.dmg» (§10). Sin repos, sin Homebrew y sin internet al instalar.

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
   nuevo cada vez que se actualice la plataforma, antes del viaje. (Con el DMG: «Instalar», §10.)
2. **Nada de secretos que teclear** en el modo automático (el normal): los secretos propios del
   nodo (base, sesiones, web↔API y **secreto de los pases del nodo**) los genera la app sola la
   primera vez y quedan en el Llavero. Lo opcional está en **Ajustes › Avanzado** (contraseña del
   Wi-Fi para el QR, token de Cloudflare; en modo manual también la conexión de producción).
3. **Certificado**: lo trae «Preparar evento» si producción lo tiene configurado. Si no, la app
   crea uno local (plan B) para poder encender. Mejor: **Certificado › Plan A (Let's Encrypt)**
   1–3 semanas antes (dura ~90 días; la tarjeta se pone ámbar si vence antes de que termine el
   evento o en menos de 30 días, y roja a 7 días).
4. **Red del evento** (antes «Configurar router»): elige cómo llegan los teléfonos a la Mac (§2.3 y
   §2.4). Cada opción termina con **Revisar**; en verde «Listo», la ventana principal lo muestra.
5. **Ensayo**: Preparar (§3), Encender, conectar 2–3 teléfonos (iPhone y Android) al Wi-Fi,
   abrir el sitio, iniciar sesión, leer un pase, capturar, apagar el Wi-Fi del teléfono, capturar,
   volver.

### 2.3 Configurar el router (una sola vez)

1. Enciende el GL.iNet (con su cable USB-C). La primera vez, en su panel (http://192.168.8.1)
   eliges idioma y **contraseña de administración**. Guárdala en el gestor de contraseñas.
2. Conecta la Mac al router: **cable** al puerto LAN del GL.iNet (recomendado) o a su Wi-Fi.
   - Si es por Wi-Fi: Ajustes del Sistema › Wi-Fi › (la red del GL.iNet) › **Detalles** ›
     *Dirección privada de Wi-Fi* → **Fija** (o desactivada). Si no, el router no la reconoce.
3. En la app, **Red del evento › GL.iNet portátil**: escribe la contraseña de administración (no se guarda), el
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

## 3. La víspera (con internet) — «Preparar evento»

### 3.1 Modo automático (recomendado)

Con internet **de verdad** (casa, iglesia o el teléfono como hotspot; no el Wi-Fi del evento):

1. Coordinación imprime los pases en producción (Clubes › Imprimir pases) y deja listas reglas,
   personal e inscripciones **en producción**.
2. **Preparar evento › Automático › Conectar con adventist.club**: correo y contraseña de tu
   cuenta de coordinación (y el código de 6 dígitos si tu cuenta tiene verificación en dos pasos).
   La sesión vive sólo en la memoria de la app: se borra al cerrar la ventana o a los 15 minutos.
3. **Mis eventos**: eliges el evento.
4. **¿Qué captura esta Mac?**: nombre del nodo (p. ej. «Zona Misión Galáctica»), sus
   **actividades** (un grupo incluye a sus hijas; las de otros nodos abiertos salen en gris con el
   nombre del nodo; «Todas» sólo si no hay otros nodos) y si tiene **mesa de llegadas**.
5. **Preparar este evento** (1–3 min): enciende la base del nodo si hace falta, descarga el paquete
   cifrado, lo carga y pone el certificado si viene. Al final: clubes, cuentas, pases, alcance y
   certificado. Desde ese momento la nube sólo deja *consultar* ese evento.
6. **Encender** y probar: un juez entra en el nodo con su contraseña de siempre y lee un pase.

Si la app dice **«Esta Mac está en la red del evento»**: estás conectado al Wi-Fi del nodo (ahí
`api.adventist.club` es la Mac). Conéctate a otra red con internet y repite.

### 3.2 Modo manual (respaldo, sin la API)

Para cuando la API no está disponible: **Preparar evento › Manual**. Necesita en **Ajustes ›
Avanzado** la **conexión de producción** (`postgresql://…`) y, recomendado, el **secreto de los
pases de producción** (`PROD_EVENT_PASS_SECRET`; con él los pases se vuelven a cifrar con el
secreto propio del nodo). Campos: evento (id), correo que firma, nombre del nodo, actividades (ids,
vacío = todas) y mesa de llegadas. **1. Simular** → **2. Copiar al nodo y bloquear la nube**.

> Instalaciones viejas: si ya había un `EVENT_PASS_SECRET` guardado (el de producción) se conserva
> como secreto del nodo y todo sigue funcionando como antes.

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
| **Dispositivos conectados** | Cuántos teléfonos usaron el sitio en 10 min (con su IP real) | «Ver dispositivos» (§14): quién es quién, alertas, bloquear. |
| **Interferencias** | VPN, Little Snitch/LuLu, firewall o DNS que pueden estorbar | Ámbar/rojo: «Revisar interferencias» (§11) y seguir la guía. |
| **Seguridad de la Mac** | FileVault, firewall, compartir, Wi-Fi con contraseña | Ámbar: «Ver seguridad» (§15). |
| **Nodos vecinos** | Otros nodos del mismo evento en la red y su última sincronización | Sólo informativo (§13). |
| **Energía** | Cargador o batería | Ámbar/rojo «Con batería»: **conecta el cargador**. |
| **La Mac no se dormirá** | El nodo impide el reposo | **No cierres la tapa** (la pantalla sí puede apagarse). |

- Los respaldos quedan en tu **carpeta personal › Nodo Camporee › Respaldos** y se copian a la USB cada 10
  min. **Cada noche:** copia la carpeta `nodo-camporee` de la USB a la segunda memoria y guárdala aparte.
- Antes de algo delicado (cerrar el evento): tarjeta **Respaldo › Respaldar ahora**.
- Puedes cerrar la ventana: el nodo sigue encendido. Para apagar: abre la app › **Apagar** (hace un
  último respaldo primero).

## 6. Subir resultados (avance o final)

### 6.1 Modo automático

1. Con internet de verdad (o, en la «Red pública del evento», con el WAN del GL.iNet conectado:
   la Mac recibe DNS públicos y es la única que sale), **Subir resultados › Automático › Conectar**.
   El evento ya viene elegido (el de este nodo).
2. Elige **Subir avance (sigo capturando)** o **Subir final (cierro este nodo)**.
3. **1. Revisar (no cambia nada)**: dice qué se subiría (nuevos, cambios) y los **conflictos**
   (algo cambió en la nube Y en el nodo) en palabras. Con conflictos: **Dejar pendientes**,
   **Gana el nodo** o **Gana la nube**.
4. **2. Subir**: primero hace un respaldo y sube todo en una sola operación. Al final dice si la
   nube quedó **liberada** o cuántos nodos siguen abiertos (se libera cuando todos suben su final).
5. Repetirlo no duplica nada. Después del final: **Apagar** y guarda la USB de respaldos.

### 6.2 Modo manual

**Subir resultados › Manual**: final o avance, **1. Revisar** → **2. Subir a producción**, con la
conexión de producción (recomendado: rama de respaldo en Neon antes).

## 6A. Varios coordinadores / varios nodos

Varios coordinadores en distintas zonas, cada uno con **su Mac y su router**, el mismo evento y
**actividades distintas** (la nube no deja que dos nodos abiertos compartan una actividad; mesas de
llegadas sí puede haber varias). La ventana principal dice siempre «Este nodo: «nombre» · captura:
… · llegadas: sí/no · estado: preparado / subió avance / subió final».

**Guía del día:**

| Cuándo | Qué hace cada coordinador |
|---|---|
| **La víspera** | Preparar evento › Automático, con **su alcance** (sus actividades, llegadas sí/no). Red del evento en su router (un Wi-Fi con nombre distinto por zona: «Camporee Zona A»). |
| **Durante** | Sus jueces capturan en el Wi-Fi de su zona. Un juez que cambia de zona vuelve a iniciar sesión allá; si tiene capturas «por enviar», primero regresa a su zona a mandarlas. |
| **Si hay señal** | Subir resultados › **Subir avance** (el nodo sigue abierto). |
| **Al terminar** | Subir resultados › **Subir final**. |
| **El administrador** | Ve los nodos del evento en la nube (estado, último envío, conflictos) y puede **cerrar** un nodo que no volverá (lo no subido de ese nodo se pierde para la nube). |

## 6B. Usar una red existente: Google Wifi / Nest Wifi (y otros routers)

En **Red del evento** hay cinco opciones; todas terminan con el mismo **Revisar** (el DNS del
router dice que events.adventist.club es esta Mac, si los equipos se ven entre sí y si hay
internet) y un **«Listo»** verde que se ve en la ventana principal:

- **GL.iNet portátil (recomendado)** — §2.3. El DNS de la Mac no hace falta.
- **Red pública del evento** — §6C.
- **Google Wifi / Nest Wifi** — la app enciende un **DNS en esta Mac** y muestra los pasos de
  Google Home: (1) Mac en la red **principal** (no la de invitados: aísla a los teléfonos), mejor
  por cable a un punto Wifi; por Wi-Fi, «Dirección privada de Wi-Fi» en **Fija** o desactivada;
  (2) Google Home › Wi-Fi › Configuración de red › Configuración avanzada de redes › **Reservas de
  IP** para esta Mac (la app muestra IP y MAC); (3) ahí mismo **DNS › Personalizado** = la IP de
  esta Mac (sin 8.8.8.8 de secundario); (4) **IPv6 desactivado**; (5) Encender el DNS de esta Mac
  y **Revisar**.
  - **Hazlo en casa con internet** días antes; en el evento los puntos Wifi necesitan corriente.
  - **Prueba sin internet en casa:** desconecta el cable de internet (WAN) del punto Wifi
    principal, conecta un teléfono y abre https://events.adventist.club.
  - Mientras el DNS del Google Wifi apunte a la Mac, si la Mac se apaga **nadie en esa red tiene
    internet**: al terminar, DNS › Automático y «Apagar» el DNS de la Mac.
- **Otro router con DNS personalizado** — MikroTik u OpenWrt: la app lo configura por SSH (reserva
  + DNS estático, como el GL.iNet; «Simular» muestra las órdenes). Otros: pasos a mano (reserva de
  IP, DNS de la red = esta Mac, IPv6 apagado, red principal) + DNS de esta Mac.
- **Red del lugar** — «¿Puedo usar esta red?»: revisa internet, aislamiento y DNS y recomienda
  **«Usa la nube»** (hay internet estable), **«Configura el DNS en tu router»** (lo administras) o
  **«Usa tu GL.iNet»** (la red aísla a los equipos o no la controlas: su cable al **WAN** del
  GL.iNet y los teléfonos al Wi-Fi del GL.iNet).
- **Varios nodos** — la guía de §6A.

> Si una **VPN** (NordVPN, Proton VPN…) o Little Snitch interceptan el DNS de la Mac, el DNS del
> nodo no puede contestar: la app lo detecta («Otra app intercepta el DNS») — ciérralas durante el
> evento.

## 6C. Red pública del evento (GL.iNet, OpenWrt, MikroTik)

Un Wi-Fi **abierto** (o con clave sencilla) para todos que **sólo abre el sitio del evento**.
Conexión recomendada: **módem del lugar → WAN del GL.iNet → Wi-Fi del evento**; la Mac **por
cable** a un puerto LAN. La app lo aplica por SSH (Ver qué cambiará → Aplicar; **Quitar** lo
deshace y deja lo normal):

- DNS con lista permitida: sólo events/api/admin/media.adventist.club y nodo.local → la Mac;
  **todo lo demás → 0.0.0.0**.
- Nadie (salvo la Mac) puede usar otro DNS (puertos 53/853) ni DNS cifrado conocido (DoH por IP).
- **Aislamiento de clientes** en el Wi-Fi (los teléfonos no se ven entre sí; la Mac por cable sí).
- La Mac recibe **DNS públicos** por DHCP y, opcional, es **la única que sale a internet** (para
  «Subir avance» si el WAN tiene internet).
- **«¿Hay internet?»** de los teléfonos (captive.apple.com, connectivitycheck.gstatic.com,
  msftconnecttest…): el router los manda a la Mac y Caddy contesta por http según la opción:
  - **Contestar «sí» (recomendado):** iPhone marca la red como buena y se queda en el Wi-Fi sin
    preguntar. Android pasa la prueba http pero su prueba https (no se puede imitar) falla: puede
    decir «conectividad limitada» → «Mantener conexión». Sin este «sí», Android deja los **datos
    móviles** como red principal y el navegador podría ir a la nube por datos.
  - **Página de bienvenida:** el teléfono abre su ventanita de «Iniciar sesión en la red» con un
    botón «Entrar a events.adventist.club». En iPhone esa ventanita es un mini navegador (sin
    cámara ni caché): el botón abre el sitio de verdad. Si el usuario la cierra, iPhone puede
    ofrecer «Usar otra red».
  - **Nada:** el teléfono dirá «sin internet» (apagar datos móviles a mano).
- MikroTik: DNS, firewall y DNS de la Mac los pone la app; el nombre/clave del Wi-Fi y el
  aislamiento («default-forwarding=no» / «client isolation») van a mano (dependen del paquete de
  Wi-Fi de RouterOS). Límite por cliente: no se configura (la API ya limita por cuenta).
- **Módem de Telmex/Totalplay/Izzi:** casi nunca dejan DNS propio ni bloqueo: su cable al WAN del
  GL.iNet. **Google Wifi:** DNS personalizado sí, lista permitida y bloqueo de otros DNS no.
  **UniFi/Omada:** reserva, DNS de la red = Mac, aislamiento de clientes en el SSID y una regla que
  sólo deje salir a la Mac; lista permitida no (el DNS de la Mac contesta los del evento).
- **Revisar** además comprueba que un dominio cualquiera (example.com) NO resuelve y que la prueba
  «¿hay internet?» llega a la Mac.

## 7. Si algo falla

| Problema | Solución |
|---|---|
| Un teléfono no abre el sitio | ¿Está en el Wi-Fi del GL.iNet? Si Android dice «sin internet», elegir **Mantener conexión** y apagar los **datos móviles**. Apagar VPN y «DNS privado» (Android: Ajustes › Redes › DNS privado › Automático/Desactivado). |
| El teléfono ya estaba dentro del sitio en internet | Volver a iniciar sesión: el nodo tiene sus propias sesiones (misma contraseña). |
| «Accesible en la red» en rojo | macOS u otra app bloquea la conexión: **Revisar interferencias** (§11). Firewall de macOS: Ajustes del Sistema › Red › **Firewall** › Opciones… › **Nodo Camporee** (y limactl) en *Permitir conexiones entrantes*. Si al encender aparece «¿Permitir conexiones entrantes?», pulsa **Permitir**. |
| La IP de la Mac cambió (tarjeta roja) | **Red del evento › GL.iNet** › Ver › Aplicar otra vez (pide la contraseña del router). |
| «Falta construir» | Necesita internet: Ajustes › Construir. Por eso se hace en casa. |
| El certificado vence o falla | En casa con internet: Certificado › Obtener. Último recurso sin internet: **Plan B (certificado local)**; cada iPhone debe abrir `http://<IP de la Mac>/nodo-ca.crt` (aparece como QR en «QR para teléfonos»), instalar el perfil y activarlo en Ajustes › General › Información › *Ajustes de confianza de certificados*. |
| La Mac pide permiso para «volumen extraíble», «Documentos» o conexiones entrantes | Pulsa **Permitir** (la app copia respaldos a la USB; al «Construir» lee el código si está en Documentos). |
| La Mac se apagó (batería) | Conéctala, ábrela, **Encender**. Los datos están en el disco; los teléfonos reenvían solos lo que tenían en cola. |
| La base se dañó | Técnico: `nodo-evento/nodo.sh restaurar ultimo.dump` con el `.env` del nodo, o pedir ayuda. Los respaldos están en la carpeta personal (Nodo Camporee › Respaldos) y en la USB. |
| Nada funciona | Hojas de captura en papel por actividad; se capturan después en el nodo o en producción. |

## 8. Qué tiene que dar el propietario

| Qué | Cuándo | Cómo |
|---|---|---|
| Su **cuenta de adventist.club** (coordinación del evento) | Víspera y al subir | La teclea en «Conectar»; la sesión sólo vive en memoria 15 min. |
| Contraseña de administración del **router** | Al configurar la red (una vez) | La teclea en la app; **no se guarda**. |
| **Token de Cloudflare** (Zone · DNS · Edit, sólo `adventist.club`) | Sólo si la Mac saca/renueva el certificado (si no viene en «Preparar») | Ajustes › Avanzado. Después puede revocarlo. |
| *Modo manual:* **conexión de producción** y **EVENT_PASS_SECRET de producción** | Sólo si la API no está disponible | Ajustes › Avanzado (modo manual). Recomendado: rama de respaldo en Neon. |

| **Credenciales de R2** para publicar actualizaciones (§12) | Cada vez que se publique una versión | Sólo en el entorno al correr `publicar-actualizacion.sh`. |

Nunca por chat, correo, WhatsApp ni captura de pantalla.

**En la nube (una vez, lo hace el propietario):** aplicar la migración `030_event_offline_nodes.sql`
en Neon antes de desplegar el backend, y en Render (servicio de la API) poner — sólo nombres aquí —
`CLOUDFLARE_DNS_API_TOKEN` (Zone · DNS · Edit de `adventist.club`) y `LE_EMAIL` para que «Preparar»
traiga el certificado de Let's Encrypt; opcionales `NODO_SYNC_SECRET` (si no, se deriva de
`JWT_SECRET`: rotarlo invalida la caché del certificado y las subidas de nodos ya preparados),
`LE_STAGING` (sólo ensayos), `NODO_HOSTNAMES`, `NODO_BUNDLE_MAX_BYTES`, `NODO_SETTLE_SECONDS`.
Sin las dos primeras todo funciona igual, pero cada Mac usa su certificado local (plan B).

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
  app), `autoarranque` (opcional, abre la app con `--encender` al iniciar sesión), y desde v3
  `puerta` (`Nodo Camporee --puerta`: relevo :80/:443 → 127.0.0.1:8080/8443 con protocolo PROXY y
  bloqueos por IP) y `malla` (`Nodo Camporee --malla`: Bonjour de los nodos vecinos), ambos sólo con
  el nodo encendido.
- **Órdenes v3 del motor:** `dispositivos` (JSON: API + ARP + ruta del registro de Caddy),
  `cuentas bloqueadas|desbloquear CORREO`, `router-clientes`, `router-bloquear --mac M [--quitar]
  [--simular]`, `wifi-clave [--ssid S] [--simular]` (la clave nueva en `WIFI_PASSWORD`),
  `caddy-recargar`, `malla <subcomando de nodo_malla.py>`, `imagenes-estado`,
  `imagenes-actualizar [--simular] [--base URL]`. Configuración: `PUERTA`, `PUERTO_INTERNO_HTTP/S`,
  `IMAGENES_URL`. Pruebas: `pruebas/probar-analizadores.sh`, `pruebas/actualizacion-sparkle.sh`,
  `pruebas/malla-dos-nodos.sh`, `pruebas/malla-bonjour.sh`, `pruebas/v3-motor.sh`.
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
- **Modo automático (motor):** `preparar-auto --event ID --label L [--actividades id,id | --todo]
  [--llegadas | --sin-llegadas] [--reemplazar-copia] [--reemplazar-nodo ID]`, `subir-auto --event ID
  [--dry-run] [--prefer node|prod] [--final | --avance]`, `nodo-info [--event ID]`. Corren
  `python scripts/nodo_sync.py prepare|upload|node-info` en el contenedor `sync` con
  `NODO_API_TOKEN` (sólo por el entorno: la app lo pasa desde la memoria) y `NODO_API_URL`
  (localhost/127.0.0.1 se traduce a `host.docker.internal`). Las líneas `@@paso` del script se
  muestran como sub-pasos; la última `@@json` es el resumen. Códigos: 0 ok · 1 error · 2
  conflictos · 4 volver a iniciar sesión · 5 sin permiso/MFA. `nodo-info` se guarda en
  `run/nodo-info.json` para la ventana principal. El `sync` usa DNS públicos (1.1.1.1, 8.8.8.8)
  y monta `tls/actual` (escritura) para que «prepare» instale el certificado; si llegó uno, se
  reinicia Caddy (y se quita la CA local vieja); si no hay ninguno, se crea el local.
- **API para pruebas:** `NODO_API_BASE=http://127.0.0.1:8000/api/v1` o
  `defaults write club.adventist.nodo-camporee apiBasePruebas <url>` (etiqueta roja «PRUEBAS»).
  Antes de iniciar sesión la app pide `{API}/health`: si trae `X-Nodo-Local: 1` (lo pone el
  Caddy del nodo) está hablando con un nodo y se niega.
- **DNS de la Mac («Mi red»):** Colima/Lima **no reenvía UDP** al anfitrión (su reenviador de
  puertos va por SSH, sólo TCP; probado con Colima 0.10.3 / Lima 2.2.0, `vz`: TCP llegó, UDP no).
  Por eso el DNS no va en un contenedor: lo sirve el **ejecutable de la app** (`Nodo Camporee
  --dns`, `app/DNS.swift`) como LaunchAgent `…dns` (KeepAlive). macOS deja a un proceso normal
  escuchar en el puerto 53 si escucha en **todas** las direcciones (0.0.0.0): sin root, sin
  ayudante privilegiado, sin binarios extra que firmar. Contesta events/api/admin/media y
  nodo.local con la IP a la que llegó la pregunta; lo demás lo reenvía a 1.1.1.1/8.8.8.8 (nunca
  al router: sería un círculo) con 1.2 s de espera, y sin internet contesta SERVFAIL al instante
  (20 s de «sin internet» tras un fallo, tanteando cada 5 s). TCP 53 si está libre (si otro
  programa lo ocupa —p. ej. otra VM de Colima— sigue sólo por UDP). Órdenes: `dns-on [--ip IP]`,
  `dns-off`, `dns-estado`; red: `red-modo`, `red-revisar`, `red-diagnostico`,
  `red-publica-plan|aplicar|quitar [--simular] [--tipo glinet|openwrt|mikrotik]`,
  `router-plan|router-aplicar --tipo glinet|openwrt|mikrotik`.
- **media.adventist.club:** «Preparar» copia las imágenes a `datos/media/mirror/<misma ruta>`;
  Caddy las sirve (https y http) y el DNS de todas las opciones lo manda a la Mac.
- **Pruebas:** `pruebas/router-openwrt.sh` y `pruebas/red-publica-openwrt.sh` (OpenWrt real:
  uci + dnsmasq de verdad, idempotencia y «quitar»). `--prueba` acepta además `hoja:conectar`,
  `hoja:subir`, `hoja:red`, `red:glinet|publica|google|mi-red|otro|lugar|varios|abajo`,
  `modo:auto|manual`, `auto:entrar:correo|clave`, `auto:codigo:N`, `auto:evento:N`,
  `auto:actividad:N`, `auto:preparar`, `auto:final`, `auto:revisar`, `auto:preferir:node`,
  `auto:subir`, `otro:mikrotik|openwrt|manual|simular`, `publica:ssid:…`, `publica:simular`.
  Aislamiento: `NODO_COMPOSE_PROJECT`, `NODO_COMPOSE_EXTRA`, `NODO_TAG`, `NODO_DNS_PUERTO`,
  `NODO_ROUTER_DNS_PUERTO` (además de `NODO_APP_SUPPORT`, `NODO_LLAVERO_SERVICIO`, `NODO_AGENT_PREFIX`).
- **Límite conocido:** con Colima todas las conexiones llegan a Caddy desde una IP interna, así
  que la API ve a todos los teléfonos con la misma IP (el límite por IP del nodo ya es 200/min y el
  límite por cuenta sigue igual). Por lo mismo, «equipos en la red» sale de la tabla ARP de la Mac.
- **Certificado plan B:** Caddy entrega la CA en `http://<ip>/nodo-ca.crt` (sólo si existe
  `tls/actual/ca-local.pem`; con Let's Encrypt responde 404).
- **CLI de la app:** `"Nodo Camporee" --motor <orden>` (con los secretos del Llavero),
  `--secreto-guardar NOMBRE` (valor por stdin), `--secreto-existe NOMBRE`, `--secreto-borrar NOMBRE`,
  `--secreto-prueba`, `--prueba` (abre la ventana con ganchos para pruebas automáticas:
  `swift pruebas/avisar.swift "hoja:qr"`, `"captura:/ruta/nombre"`, `encender`, `apagar`).
- **Versión portátil (DMG):** `empaquetar.sh` (§10). La app lleva `Contents/Resources/paquete.env`;
  con él aparece «Instalar» (`nodo-mac.sh instalar`) y «Construir» sólo si la configuración tiene
  `BACKEND_DIR`/`FRONTEND_DIR` válidos. Herramientas: `HERRAMIENTAS=sistema|app` en `config.env`
  (las de la app viven en `Application Support/Nodo Camporee/herramientas/`, con su propio
  `DOCKER_CONFIG` en `…/docker/`). Para pruebas: `NODO_HERRAMIENTAS=app`, `NODO_APP_SUPPORT`,
  `NODO_LLAVERO_SERVICIO`, `NODO_AGENT_PREFIX` (otro Llavero y otros LaunchAgents) y
  `NODO_COLIMA_EXTRA` (argumentos extra al crear la máquina de Colima).
- **Firma:** `instalar.sh` y `empaquetar.sh` firman con el Developer ID del propietario si está en
  el Llavero (runtime endurecido + sello de tiempo; macOS reciente rechaza una app ad-hoc
  reinstalada) y notarizan si existe el perfil `nodo-camporee` de notarytool.
- **Pruebas sin router:** `pruebas/router-openwrt.sh` corre el guion del router dos veces en un
  OpenWrt real (imagen `openwrt/rootfs`, uci de verdad) y comprueba reserva, DNS, Wi-Fi, que no
  toca lo demás y que repetirlo no cambia nada.

---

## 10. Otra Mac: el DMG portátil (sin repos, sin internet al instalar)

Para tener una **Mac de respaldo** (o que el nodo lo opere otra persona en su Mac) no hace falta
clonar repos, instalar Homebrew ni tener internet: todo viaja en **un archivo**,
`Nodo Camporee.dmg` (~0.9 GB).

### 10.1 Qué trae el DMG

La app «Nodo Camporee» y, dentro de ella:

| Qué | Para qué |
|---|---|
| Los archivos del nodo (compose, Caddyfile, scripts de respaldo y certificado) | Lo mismo que copiaba `instalar.sh` |
| Las imágenes de Docker ya construidas: API, sitio web, Postgres, Caddy y lego | El sistema completo, del mismo commit que producción (`docker load`, sin internet) |
| Colima, Lima, Docker y Docker Compose (+ sus licencias) | Docker sin Homebrew ni Docker Desktop |
| La imagen de la máquina virtual de Colima | Para crearla la primera vez sin descargar nada |

**Sólo Macs con chip Apple (M1 o más nueva).** Una Mac **Intel** necesitaría otro paquete: imágenes
`linux/amd64`, Colima/Lima/Docker para Intel y la imagen de máquina virtual amd64. `empaquetar.sh`
hoy se niega a hacerlo (no hay una Mac Intel para probarlo); si se necesita, avisar al técnico.

### 10.2 Hacer el DMG (técnico, en la Mac con los repos, con internet)

```sh
cd backend-repo                                   # en el MISMO commit que producción
FRONTEND_DIR=../frontend ./nodo-evento/mac/empaquetar.sh --salida ~/Desktop
```

Construye las imágenes (exportándolas a archivo: no cambia las del nodo de esa Mac), copia las
herramientas, firma todo con el Developer ID del propietario y, si existe el perfil
`nodo-camporee`, lo **notariza** y lo grapa. Si no existe, avisa y deja el DMG firmado pero sin
notarizar. Para crear el perfil (una sola vez, lo hace el propietario en su Terminal; pide la
«contraseña específica de app» de su Apple ID, nadie más la ve):

```sh
xcrun notarytool store-credentials "nodo-camporee" --apple-id <correo-del-Apple-ID> --team-id K8KM5TV738
```

Rehacer el DMG cada vez que cambie la plataforma (como «Construir»).

### 10.3 Pasarlo a la otra Mac

- **AirDrop:** clic derecho sobre el DMG › Compartir › AirDrop. (Tarda unos minutos.)
- **Memoria USB:** copiar el DMG a la memoria y de ahí al Escritorio de la otra Mac. Sirve la
  `NODO-RESPALDO` (formato APFS o exFAT; en FAT32 no cabe un archivo de más de 4 GB, el DMG sí cabe).

### 10.4 Instalar en la otra Mac (sin internet)

1. Doble clic en `Nodo Camporee.dmg`. Se abre una ventana: **arrastra «Nodo Camporee» a
   «Aplicaciones»**. Expulsa el DMG.
2. Abre **Aplicaciones › Nodo Camporee** (no desde la ventana del DMG: ahí el botón «Instalar»
   queda gris y la app lo dice).
   - Si el DMG está **notarizado**, macOS sólo pregunta «¿Abrir?» → **Abrir**.
   - Si **no** está notarizado, macOS dice que no puede verificarla: Ajustes del Sistema ›
     **Privacidad y seguridad** › abajo, «Nodo Camporee» › **Abrir de todos modos** (una vez).
3. Pulsa **Instalar** (2–5 min, sin internet). Copia los archivos del nodo a
   `~/Library/Application Support/Nodo Camporee/`, deja Colima y Docker listos (si la Mac ya
   tiene Colima de Homebrew, usa ése), crea la máquina de Docker «nodo» y carga el sistema.
4. **Ajustes › Secretos** (§10.5) y **Certificado** (§10.6).
5. **Encender**, y lo demás igual que en la Mac principal (§2.2 a §7). «Construir» no aparece
   (no hay repos): para actualizar, se instala un DMG nuevo y se pulsa **Instalar** otra vez (los
   datos, el certificado y los secretos se conservan).

Si macOS pregunta por «conexiones entrantes» de **limactl** al encender, **Permitir** (§7).

### 10.5 Secretos que hay que poner en la otra Mac

Cada Mac guarda los suyos en su propio Llavero; no viajan en el DMG. En **modo automático no hay
que teclear ninguno**: la otra Mac se prepara con «Conectar con adventist.club» (su propio nodo).

| Secreto | ¿Hace falta? |
|---|---|
| Base, sesiones, web↔API y **secreto de los pases del nodo** | Nada: la app los genera sola |
| **Conexión de producción** + **secreto de pases de producción** | Sólo en **modo manual** |
| **Token de Cloudflare** | Sólo si en esa Mac se va a **sacar o renovar** el certificado. Si se copia de la Mac principal (§10.6), no |
| Contraseña del Wi-Fi del evento | Opcional (QR «Conéctate al Wi-Fi», configurar router) |

Nunca por chat, correo, WhatsApp ni captura de pantalla: se teclean en la app desde el gestor de
contraseñas.

> Cada Mac tiene su propia firma de sesiones: si a mitad del evento se cambia de Mac, los jueces
> vuelven a iniciar sesión (misma contraseña). Los datos capturados se pasan con el último respaldo
> (`ultimo.dump` de la USB) y los restaura el técnico; las capturas en cola de los teléfonos se
> reenvían solas.

### 10.6 Copiar el certificado en vez de pedir otro

El certificado de Let's Encrypt vale para cualquier Mac del nodo: **cópialo** de la Mac principal
(no hace falta internet ni el token de Cloudflare, y no gasta solicitudes a Let's Encrypt).

En la Mac principal, Finder › Ir › **Ir a la carpeta…** (⇧⌘G):
`~/Library/Application Support/Nodo Camporee/nodo/tls/actual/` → copia `fullchain.pem` y
`privkey.pem` (y `ca-local.pem` si se usa el plan B) a la memoria USB.

En la otra Mac (después de «Instalar»), la misma carpeta con ⇧⌘G → pega los archivos. O en la
Terminal:

```sh
D=~/Library/Application\ Support/Nodo\ Camporee/nodo/tls/actual
mkdir -p "$D" && cp /Volumes/NODO-RESPALDO/fullchain.pem /Volumes/NODO-RESPALDO/privkey.pem "$D"/
chmod 600 "$D/privkey.pem"
```

Luego **borra los `.pem` de la memoria USB**: `privkey.pem` es la llave del certificado. La tarjeta
«Certificado» de la app se pone verde con la misma fecha de vencimiento que en la Mac principal.
Con el plan B (CA local) copia también la carpeta `…/nodo/tls/ca-local/` si quieres emitir más
certificados con la misma CA (los teléfonos ya confían en ella).

### 10.7 Prueba hecha (15 oct 2026)

En la Mac del propietario, simulando una Mac nueva: otra carpeta personal (`HOME` aparte, sin
Colima, sin caché ni repos), el DMG marcado como descargado (cuarentena) e instalado en su propia
carpeta de Aplicaciones; herramientas forzadas a «las de la app»; Llavero y LaunchAgents de prueba
aparte. «Instalar» desde la app: copió el nodo y las herramientas, sembró la imagen de la máquina
virtual (las descargas del lado de la Mac apuntaban a un proxy muerto) y cargó las 5 imágenes.
Luego, con la máquina virtual **sin salida a internet** (iptables: sin DNS ni tráfico fuera de la
red interna) se borraron las imágenes y se repitió «Instalar» (las volvió a cargar), certificado
local, **Encender** → base, API, sitio y HTTPS sanos (`/nodo-salud`, `/auth/login`,
`/api/v1/health` = 200, ENCENDIDO en verde) y **Apagar** (con su último respaldo). La
configuración no tenía ninguna ruta de repos y «Construir» no apareció. `spctl`: firma válida,
«Unnotarized Developer ID» (falta el perfil de notarización). No se probó en una Mac Intel ni con
el router real.

### 10.8 Prueba hecha (modo automático y red, 15 oct 2026)

App compilada aparte (otro Llavero, otros LaunchAgents, otra carpeta de datos y otro proyecto de
compose; la instalación real no se tocó). Con una API de prueba (formas del contrato, con y sin
código de verificación) y un `sync` sustituto: Conectar → código → Mis eventos → alcance (otro nodo
en gris) → Preparar (certificado instalado / conservado / local), Subir avance y final con
conflicto («gana el nodo»), sesión vencida (código 4 → volver a iniciar sesión), y el aviso «Esta
Mac está en la red del evento» (`X-Nodo-Local`). Con la API local del backend y el `nodo_sync.py`
real (imagen `nodo-evento-api:login`): `preparar-auto` (6 clubes, 13 cuentas, 6 pases),
`subir-auto --dry-run --final` y `--final` (nodo cerrado, nube liberada). Caddy: `X-Nodo-Local`,
`media.adventist.club` (200/404) y las tres respuestas de portal. DNS de la Mac: respuestas propias,
reenvío, TCP, y sin internet SERVFAIL en 1.2 s la primera vez y ~0.02 s después; puerto 53 se abre
sin root (en la Mac de pruebas una VPN intercepta el DNS saliente, por eso la prueba punta a punta
fue en otro puerto; la app detecta esa intercepción). OpenWrt real: guion normal y red pública
(dnsmasq: permitidos → nodo, cualquier otro → 0.0.0.0), idempotentes, y «quitar». MikroTik: sólo
simulación (sin RouterOS a la mano). No se probó con un Google Wifi real ni con teléfonos.

---

## 11. Interferencias (VPN, Little Snitch, firewall, DNS)

La luz **Interferencias** se revisa sola cada 5 minutos y al pulsar **Encender** (si encuentra algo,
avisa y deja encender igual). **Revisar interferencias** muestra cada hallazgo con qué hacer, en
palabras sencillas. La app **nunca** cambia ajustes de esas apps ni de macOS. Detecta, con
herramientas de Apple (`scutil --dns`, `scutil --nc list`, `ifconfig`, `netstat -rn`,
`systemextensionsctl list`, `socketfilterfw`, `networksetup`, `pgrep`):

| Hallazgo | Qué decir al operador |
|---|---|
| Túnel VPN activo (utun con ruta por defecto o DNS propio) | Desconecta la VPN durante el evento. |
| **NordVPN** (app, extensión `com.nordvpn.*`, NordLynx, Meshnet, Threat Protection/DNS propio) | «NordVPN está conectado: desconéctalo durante el evento»; apaga también Meshnet y Threat Protection (cambian el DNS). |
| Tailscale, WireGuard, Proton VPN, Cloudflare WARP, Mullvad, AnyConnect… | Desconéctalos o ciérralos durante el evento. |
| **Little Snitch** (extensión `at.obdev.littlesnitch.*` activa) | «Little Snitch puede bloquear a los teléfonos: permite conexiones ENTRANTES a “Nodo Camporee” (puertos 80 y 443, y 53 con “Mi red”) y a limactl», o modo silencioso «Permitir». |
| LuLu u otro filtro de red | Igual: permitir entrantes a Nodo Camporee o pausar el filtro. |
| Firewall de macOS que bloquea todo, o sin permiso para Nodo Camporee/limactl | Ajustes › Red › Firewall › Opciones… › Permitir. |
| DNS de la Mac que no es el router (manual o de una VPN) | Volver a «Automático» en Ajustes › Red › (interfaz) › DNS. |
| Relay privado de iCloud | No se puede comprobar solo: desactívalo para la red del evento. |

Las pruebas usan salidas reales capturadas en una Mac de pruebas (con NordVPN, Proton y Little Snitch
instalados) y otras escritas según la documentación pública: `pruebas/probar-analizadores.sh`
(125 pruebas, `pruebas/muestras/LEEME.txt` dice de dónde sale cada una).

## 12. Actualizaciones (Sparkle)

- **Ajustes › Actualizaciones › Buscar actualizaciones** (y en el menú de la app). Además revisa
  sola **una vez al día** cuando hay internet (sin internet no pasa nada).
- **Nunca con el nodo ENCENDIDO**: con el nodo encendido no revisa sola, y si alguien pulsa
  «Instalar», la instalación **espera a «Apagar»** (aviso «se instala al pulsar Apagar»).
- **Chicas:** la actualización es sólo la app (pocos MB). Las imágenes de Docker (~540 MB) van
  aparte, por versión (`imagenes.json` dentro de la app, una entrada por imagen con su Id y sha256):
  al «Instalar» después de actualizar sólo se cargan las que **cambiaron**, tomándolas de la caché de
  la Mac (`Application Support/Nodo Camporee/imagenes/`, la llena el DMG), y si no están, de
  internet (`https://media.adventist.club/nodo/imagenes/<versión>/`, verificadas por sha256 contra el
  manifiesto firmado dentro de la app). Sin internet y sin caché: sigue con las anteriores y avisa;
  el **DMG sigue siendo el instalador completo sin internet**.
- Cada actualización va firmada con el Developer ID (notarizada) **y** con la llave EdDSA de
  Sparkle (`SUPublicEDKey` en la app; la privada sólo en el Llavero del propietario, cuenta
  `nodo-camporee` de `generate_keys`). Instalada desde el repo (`instalar.sh`): sin revisión automática.

**Publicar una versión (técnico, con las credenciales del propietario):**

```sh
FRONTEND_DIR=../frontend ./nodo-evento/mac/empaquetar.sh --salida ~/Documents/DEEL/_dev-eventos/dist
#  → dist/Nodo Camporee.dmg (completo) y dist/actualizaciones/ (zip ligero, appcast.xml, imagenes/<ver>/)
./nodo-evento/mac/publicar-actualizacion.sh --simular        # qué subiría
R2_ACCOUNT_ID=… R2_ACCESS_KEY_ID=… R2_SECRET_ACCESS_KEY=… R2_BUCKET=… \
  ./nodo-evento/mac/publicar-actualizacion.sh                 # imágenes nuevas → zip → appcast.xml
```

**Qué debe dar el propietario:** una llave de API de R2 con «Object Read & Write» **sólo** en el
bucket que sirve `media.adventist.club` (id de cuenta, access key, secret y nombre del bucket), y
confirmar que `https://media.adventist.club/nodo/…` sirve la carpeta `nodo/` de ese bucket. Las
credenciales se pasan sólo en el entorno al publicar; nunca se guardan. Alternativa: GitHub Release
(empaquetar con `NODO_ACTUALIZACIONES_URL=https://github.com/<repo>/releases/download/<tag>` y
subir los archivos con `gh release upload`; el `SUFeedURL` de la app tendría que apuntar ahí).
**Respaldo de la llave EdDSA:** `generate_keys --account nodo-camporee -x archivo` y guardarlo en el
gestor de contraseñas (si se pierde, las apps instaladas no aceptarán actualizaciones nuevas).

## 13. Nodos vecinos (varias Macs del mismo evento en una red)

Cuando los nodos de varios coordinadores del **mismo evento** comparten red (p. ej. un Google/Nest
Wifi en todo el lugar), cada uno ve el avance de los otros **sin la nube**: la clasificación del
panel de coordinación queda completa en cada zona.

- La app anuncia el nodo por **Bonjour** (`_nodocamporee._tcp`) y encuentra a los demás del mismo
  evento. La primera vez macOS pregunta si «Nodo Camporee» puede **encontrar dispositivos en la red
  local**: **Permitir** (si no, la tarjeta lo dice: Ajustes › Privacidad y seguridad › Red local).
- Cada 30 s se intercambian los **avances firmados** (mismo formato que la subida a la nube),
  autenticados con las llaves Ed25519 de cada nodo. Los nodos preparados con la nube se reconocen
  solos (la nube «avala» a cada nodo, también a los preparados después); un nodo manual se
  **empareja** con un código QR/texto y una huella de 6 dígitos (Nodos vecinos › Emparejar).
- Lo recibido queda de **sólo lectura** (actividades que este nodo no captura). Si algo se traslapa
  (dos nodos con lo mismo) se **reporta** y no se aplica. Un nodo desconocido o sin firma se rechaza.
- La **subida a la nube no cambia**: cada nodo sube sólo lo suyo y la nube sigue siendo la fuente
  de verdad después del evento. Detalles técnicos: `../README.md` §11A.
- **Administración remota por internet (no incluida, sólo documentada):** si el operador necesita
  llegar a un nodo desde fuera, usar **Tailscale** (o WireGuard) en la Mac del nodo y en la del
  operador, con una ACL que sólo permita al operador → nodo (SSH o pantalla compartida). Nunca abrir
  puertos del router hacia internet. Ojo: Tailscale/WireGuard activos aparecen en «Interferencias»
  (apagarlos mientras los teléfonos usan el nodo si cambian el DNS).

## 14. Dispositivos conectados, IP real y bloqueos

- **IP real de cada teléfono:** la app escucha en los puertos 80/443 de la Mac («puerta», relevo
  con el protocolo PROXY) y Colima sólo publica en `127.0.0.1:8080/8443`. Así Caddy, los registros y
  la API ven la IP de cada teléfono (límites por dispositivo). Funciona igual con GL.iNet, Google
  Wifi y las demás opciones (el DNS sigue contestando la IP de la Mac). Para volver a lo de antes:
  `config set PUERTA 0`. Caddy sólo cree la línea PROXY si viene de `NODO_PROXY_PERMITIDOS` (con la
  puerta el motor pone loopback y las redes de Colima/Docker; por defecto, nadie): sin la puerta, un
  teléfono no puede inventarse otra IP para saltarse el límite por dispositivo.
- **Dispositivos conectados** (tarjeta › Ver dispositivos): IP, MAC y nombre (con la contraseña del
  router, de su lista DHCP), fabricante si se sabe, cuenta con sesión o «sin sesión», peticiones por
  minuto, inicios de sesión fallidos y **alertas** (muchos fallidos, escaneo de rutas, demasiadas
  peticiones, muchas cuentas probadas). Datos del registro JSON de Caddy (`datos/caddy/acceso.log`)
  y de la API del nodo; nada sale de la Mac.
- **Bloquear / Desbloquear** (con motivo): siempre en el nodo (la puerta corta la conexión y Caddy
  contesta 403) y, en GL.iNet/OpenWrt/MikroTik, también en el router por MAC (lista negra del Wi-Fi;
  el teléfono se expulsa sin desconectar a los demás). Google Wifi: Google Home › Dispositivos ›
  Pausar. Cada bloqueo queda en `Application Support/Nodo Camporee/auditoria-bloqueos.jsonl`.
- **Cuentas frenadas:** en el nodo, un teléfono que se equivoca de contraseña muchas veces sólo se
  frena **a sí mismo** para esa cuenta (5 intentos / 5 min); la cuenta se bloquea en todos lados sólo
  tras 60 intentos en 15 min, y un teléfono que prueba muchas cuentas se frena (20 / 5 min). Si un
  juez no puede entrar: **Desbloquear cuenta** (queda auditado). Ver `docs/RATE_LIMITS.md`.

## 15. Seguridad de la Mac y Wi-Fi del evento

**Ver seguridad** revisa (sólo lectura, sin pedir contraseña): FileVault, firewall + modo encubierto
+ quién puede recibir conexiones, compartir archivos/pantalla/sesión remota (SSH)/gestión remota/
contenido/impresoras apagados, inicio de sesión automático apagado, bloqueo de pantalla y AirDrop en
«Solo contactos» o apagado. Cada punto dice dónde se ajusta en Ajustes del Sistema.

**Wi-Fi del evento:** recomendado con contraseña **WPA2** que sólo tengan jueces, coordinación y
directores (no abierto). **Cambiar contraseña del Wi-Fi** (GL.iNet/OpenWrt/MikroTik, con la
contraseña del router, que no se guarda): «Ver qué cambiará» y luego cambiar; la nueva queda en el
Llavero y el QR «Conéctate al Wi-Fi» la usa. Google Wifi: desde Google Home.
