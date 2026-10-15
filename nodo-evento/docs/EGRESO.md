# Qué sale de una Mac con «Nodo Camporee» (egreso)

Pedido del dueño (15 oct 2026), después de que una llave de Google pasada como argumento en su Mac
quedara en una muestra de pruebas (`ps -axo … args`) y GitHub bloqueara el push:
«asegurémonos de que no salgan secretos de mi Mac, ni de los demás, cuando se suban cosas desde la app».

Regla: **de una Mac nodo sólo salen datos del evento**, armados desde una lista blanca. Nunca
listas de procesos, variables de entorno, nombres del equipo o del usuario, MAC, rutas personales,
contraseñas del Wi-Fi, cosas del Llavero ni tokens. Lo que se muestra en pantalla (y se puede
copiar) pasa antes por el **depurador**.

## 1. Inventario

| # | Camino | Qué se manda | A quién | ¿Puede llevar datos de la Mac? | Control |
|---|---|---|---|---|---|
| 1 | **Subir avance / final** (`subir-auto` → `nodo_sync.py upload` → `POST /events/{id}/offline/import`) | Sobre cifrado (X25519, llave por preparación) y firmado (Ed25519) con: filas de las 9 tablas del evento, cuentas que esas filas mencionan, bitácora del evento desde la copia, pases en claro (para recifrarlos). Cabeceras: `Authorization` (token del coordinador, sólo en memoria), `X-Nodo-Timestamp/Signature`, `User-Agent: nodo-camporee/1`. | api.adventist.club | Antes sí: la **etiqueta del nodo** era el nombre de la Mac; la bitácora llevaba la **IP** (teléfono o la propia Mac) y texto libre. | Lista blanca `backend/app/services/nodo_egreso.py`: el nodo arma el paquete con `sanitize_changes` (sólo claves/tablas/columnas permitidas, bitácora sólo de entidades del evento, IP = null, texto depurado y sin los nombres de la Mac) y la nube lo **rechaza (422)** con `validate_changes` si trae una clave, tabla o columna desconocida, una bitácora con IP o con datos del equipo, o algo con forma de secreto. |
| 2 | **Preparar evento** (`preparar-auto` → `POST …/offline/export`) | Llave pública de un solo uso, llave de firma del nodo, alcance, **etiqueta**, nodo a reemplazar. | api.adventist.club | La etiqueta (antes, el nombre de la Mac por defecto). | La app propone «Nodo N»; nunca el nombre del equipo. |
| 3 | **Modo manual** (`exportar` / `importar`, `nodo_sync.py` con `PROD_DATABASE_URL` del Llavero) | Lo mismo que 1, directo a la base de producción. | Neon | Igual que 1. | Mismo `gather_node_changes` (lista blanca) e `import_changes` valida antes de leer nada. |
| 4 | **Iniciar sesión** (app, `API.swift`) | Correo, contraseña, código MFA; `GET /health`. Sesión efímera, sin cookies ni caché, `User-Agent: NodoCamporee-Mac/1`. | api.adventist.club | No. | El token vive en memoria y pasa al motor por el entorno de ESA orden. |
| 5 | **Nodos vecinos — Bonjour** | Anuncio `_nodocamporee._tcp` «Nodo <etiqueta>» con TXT `ev` (evento), `n` (id del nodo), `l` (etiqueta). | Toda la red local del evento | La etiqueta. | Etiqueta neutral (ver 2). |
| 6 | **Nodos vecinos — avances** (`/nodo-malla/v1/…`, cada 30 s) | `hola`: identidad pública (evento, nodo, etiqueta, alcance, llave de firma, aval de la nube). Avance cifrado y firmado: sólo `event_check_ins`, `evaluations`, `evaluation_revisions`, `event_adjustments` propias + cuentas como `{id, name}`; sin bitácora ni pases. | Otros nodos del mismo evento (y `hola` lo puede leer cualquiera en el Wi-Fi) | No (mismo armado que 1). | `sanitize_changes(avance=True)` al armarlo; quien lo recibe lo valida (`validate_changes(avance=True)`) y lo rechaza si trae otra cosa. |
| 7 | **Sparkle** (una vez al día, con el nodo apagado) | `GET` del appcast; descarga del zip. `User-Agent` de Sparkle (app + versión). `SUEnableSystemProfiling` = **no** (sin modelo, CPU, idioma…). | media.adventist.club (Cloudflare) | No (sólo la IP pública, inevitable). | `construir-app.sh`. La bitácora local `run/actualizaciones.log` pasa por el depurador. |
| 8 | **Imágenes nuevas** (`imagenes-actualizar`) | `GET` de `.tar.gz` por versión. | media.adventist.club | No. | Verificadas por sha256. |
| 9 | **Router** (SSH + uci/RouterOS, `router-plan/aplicar`, red pública, bloquear MAC, cambiar Wi-Fi) | Guiones con IP/MAC de reserva, nombres DNS, SSID y (al cambiarla) la clave del Wi-Fi por stdin. Contraseña de administración por `SSH_ASKPASS` desde el entorno (nunca en disco ni en argumentos). | El router del evento (red local) | Sí: la MAC de la Mac (necesaria para la reserva). | La salida del router pasa por `depurar.pl` antes de mostrarse; en simulación la clave sale como `KEY=<oculta>`. |
| 10 | **DNS de la Mac** («Mi red») | Reenvía lo que no es del evento a 1.1.1.1 / 8.8.8.8. | Resolutores públicos | No de la Mac (sí los nombres que piden los teléfonos). | — |
| 11 | **Sondas de red** (`red-diagnostico`) | `GET http://connectivitycheck.gstatic.com/generate_204`; `captive.apple.com` contra el router. | Google / el router | No. | — |
| 12 | **Registro de Caddy** (`datos/caddy/acceso.log`) | IP, User-Agent, URI de cada teléfono. | **Se queda en la Mac** | Sí, de los teléfonos; la URI del pase (`/p/evp_…`) es un pase. `Authorization` y `Cookie` salen `REDACTED` (comprobado con `caddy:2.10-alpine`). | Nunca se sube. «Registros» y el depurador ocultan `evp_…`, tokens y cabeceras. |
| 13 | **«Registros»** (`nodo-mac.sh registros` = `docker logs` de db/api/web/caddy/respaldo) | Texto en la ventana (seleccionable). | Pantalla / portapapeles | Sí, si una biblioteca escribiera algo así. | `depurar.pl` en el motor y `Depurador.swift` en la app (cada línea del motor). |
| 14 | **Interferencias / Seguridad / Dispositivos** | Lectura local de `ifconfig`, `netstat`, `scutil`, `systemextensionsctl`, `socketfilterfw`, `ps -axo comm=`, `arp`, `launchctl`, `defaults`… | Pantalla | Sí (MAC, IPv6 de enlace local, rutas). | El «detalle técnico» de cada hallazgo pasa por el depurador; `ps` sólo corre con columnas sin argumentos (`comm`, `pid`…): `Comando.permitido` rechaza `args`, `command`, `-f`, `-ww`… La tabla de Dispositivos muestra la MAC de cada teléfono (hace falta para bloquearlo en el router) y la Red muestra la MAC de la Mac (para la reserva en Google Home): nunca se exportan. |
| 15 | **Respaldos** (`pg_dump` cada 10 min → `~/Nodo Camporee/Respaldos` y la USB `NODO-RESPALDO`) | La base completa del nodo: hashes de contraseñas, semillas MFA de las cuentas copiadas, pases cifrados, llave del nodo cifrada con su secreto del Llavero. | **Disco externo** | No de la Mac; sí datos sensibles del evento. | Sin cifrar hoy. Recomendado: USB con APFS cifrado (Finder › Encriptar) y guardarla bajo llave. |
| 16 | **Capturas de pruebas** (`--prueba`, `pruebas/*.sh`) | PNG de la ventana, salidas de órdenes. | Disco / repo | Sí. | Con `--prueba` la app no lee del Llavero real los secretos del dueño (Wi-Fi, Cloudflare, producción): una captura del QR o una orden de prueba no los saca. Muestras: §4. |
| 17 | **Publicar actualización** (técnico, `publicar-actualizacion.sh`) | Zip de la app, appcast, `.tar.gz` de imágenes. | Bucket público de media.adventist.club | Lo que se cuele en las imágenes. | `*.dockerignore` excluyen `**/.env*`, llaves, `.npmrc`, `*.log`, `.vercel`, `.claude`. Credenciales de R2 por stdin (`curl -K -`). |
| 18 | **Informes de fallos** | La app no trae informador propio. macOS manda los suyos a Apple sólo si el usuario lo activó (Análisis). | Apple | Argumentos del proceso. | La app nunca recibe secretos por argumentos (entorno o stdin). |
| 19 | **Entorno del motor** | Lo que la app pasa a `nodo-mac.sh`. | Procesos locales (bash, docker) | Sí: si la app se abre desde una Terminal hereda su entorno (p. ej. `GOOGLE_API_KEY`). | `Motor.entornoHeredado` quita las variables con nombre de secreto (KEY, TOKEN, SECRET, PASSWORD, AUTH, DSN…) salvo `NODO_*`; los secretos del nodo llegan aparte desde el Llavero. Docker sólo recibe lo que se nombra con `-e`. |

## 2. Lista blanca de la subida y de los avances

`backend/app/services/nodo_egreso.py`:

- **Claves** del paquete: `format, event_id, node_id, export_id, copied_at, created_at, event, users, audit, passes`
  (avance: `format, event_id, node_id, label, scope, created_at, event, users, audit=[], passes=null`).
- **Tablas**: `events, event_activities, event_adjustment_types, event_staff, event_registrations, event_check_ins,
  evaluations, evaluation_revisions, event_adjustments` (avance: sólo las 4 de capturas), con **sus columnas exactas**.
- **Cuentas**: columnas de `users` (avance: `id`, `name`).
- **Bitácora**: columnas de `audit_log`, sólo entidades del evento (`EVENT*`, `EVALUATION`), `ip_address` nula,
  sin claves de equipo en `metadata_json` (`ip`, `user_agent`, `hostname`, `mac`, `env`, `args`, `path`, `headers`…).
- **Pases**: `{id de inscripción: token}`.
- Nada con forma de secreto o de rastro del equipo en ninguna fila (salvo los secretos propios del evento:
  `password_hash`, `mfa_secret`, `pass_token_hash`, `pass_token_enc`).

Una prueba compara las columnas con la base real: una migración que agregue una columna a esas tablas
falla hasta que alguien la revise y la agregue aquí.

## 3. El depurador

Tres implementaciones con el mismo criterio y los mismos casos (`mac/pruebas/depurador/casos.txt`, los
secretos de prueba se arman en tiempo de ejecución para no escribirlos en el repo):

- `mac/depurar.pl` — motor, captura de muestras, `buscar-secretos.sh` y los ganchos de git.
- `mac/app/Depurador.swift` — la app (Registros, detalle de hallazgos, historial de bloqueos, bitácora de Sparkle).
- `backend/app/services/nodo_egreso.py` (`scrub_text` / `find_secrets`) — la bitácora de la subida y la validación.

Quita o enmascara: bloques PEM privados; cabeceras `X-…-Key/Token/Secret/Signature/Auth…`, `Authorization`,
`Cookie` (el incidente: `--header X-Goog-Api-Key: AQ.…`); credenciales en URLs; `Bearer`/`Basic`; llaves y
tokens por forma (`AIza…`, `AQ.…`, `ya29.`, `ghp_/gho_/ghu_/ghs_/ghr_`, `github_pat_`, `sk-…`, `xox?-`, `AKIA/ASIA`,
`eyJ…` JWT, pases `evp_…`); contraseñas del Wi-Fi (uci `option key`, `.key=`, MikroTik `passphrase=`/`wpa2-pre-shared-key=`,
QR `WIFI:…;P:…;`); pares `NOMBRE_CON_KEY/TOKEN/SECRET/PASSWORD…=valor`; banderas `--token/--password/--api-key …`;
base64 de 32+ caracteres junto a key/token/secret/signature; IPv6 de enlace local; MAC (quedan los 2 últimos
bytes: `xx:xx:xx:xx:2b:3c`); `/Users/<nombre>` y `/home/<nombre>`; el nombre de la Mac, su nombre local, el
usuario y su nombre completo (→ `MAC-NODO`). Modo `--procesos` (`ps`): sólo el ejecutable, sin argumentos.

## 4. Muestras de pruebas y ganchos

- Capturar: `nodo-evento/mac/pruebas/capturar-muestras.sh GRUPO` (o `--salida DIR`). Siempre depura, `ps`
  sólo con `-axo comm=`, MAC → `02:00:5e:00:00:00`, quita `arguments`/`environment` de `launchctl print`, y al
  final revisa con `depurar.pl --buscar`: si queda algo, no guarda nada. No tiene modo «crudo».
- Revisar: `nodo-evento/mac/pruebas/buscar-secretos.sh` (árbol), `--staged` (índice), `--push` (lo que se sube).
  Estricto en `pruebas/muestras/**` y `nodo-evento/**/*.{txt,json,log,leases}`; en el resto del repo, sólo
  llaves/tokens con forma inconfundible. Corre gitleaks además si está instalado. Nunca imprime el valor.
- Ganchos: `nodo-evento/mac/pruebas/buscar-secretos.sh --instalar` (pre-commit y pre-push; en una rama sin
  el script avisan y no frenan).
- Si una llave real llega a un commit: **rotarla** en su servicio, aunque luego se borre de la historia.

## 5. Secretos en reposo en la Mac (revisión del 15 oct 2026, sólo lectura)

- **Llavero** (servicio `club.adventist.nodo-camporee`): contraseñas de Postgres/JWT/proxy/pases/operador
  (generadas), Wi-Fi, Cloudflare, conexión y secreto de producción (modo manual). Se leen y guardan con
  `/usr/bin/security` por tuberías (nunca argumentos). El token de la sesión y la contraseña del router sólo
  en memoria.
- `config.env` y `run/nodo.env`: **sin secretos** (rutas, IP, SSID, correos, id de evento, etiqueta).
  `run/askpass.sh` sólo lee la contraseña del entorno. `run/*.log`: sin secretos.
- `nodo/tls/actual/privkey.pem` y `nodo/tls/ca-local/ca.key`: archivos `0600` (Caddy los monta; no pueden ir
  al Llavero). No salen de la Mac salvo en la preparación (la nube entrega el certificado sellado).
- Caddy: `Authorization` y `Cookie` aparecen `REDACTED` en el registro; no se usa `log_credentials`.
  La API (uvicorn) registra método, ruta y código; ninguna ruta lleva tokens salvo la del pase (`/p/evp_…`,
  depurada al mostrarse).
- `--prueba` no lee del Llavero real los secretos del dueño; `--secreto-prueba` compara sin mostrar.
