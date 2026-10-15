#!/usr/bin/env bash
# Motor de «Nodo Camporee» (la app de Mac). La app (SwiftUI) sólo dibuja botones y luces; todo lo
# que hace pasa por este script, que también se puede correr a mano para diagnosticar:
#
#   nodo-mac.sh estado                  JSON con todo lo que muestra el panel (nunca secretos)
#   nodo-mac.sh encender | apagar       levanta / detiene el nodo (Colima + docker compose)
#   nodo-mac.sh respaldar               respaldo ahora + copia a la USB
#   nodo-mac.sh respaldo-espejo         sólo copia los respaldos a la USB (lo llama un LaunchAgent)
#   nodo-mac.sh construir               construye las imágenes (CON internet, una vez)
#   MODO AUTOMÁTICO (recomendado; la app inicia sesión en adventist.club y pasa el token por el
#   entorno NODO_API_TOKEN, sólo en memoria):
#   nodo-mac.sh preparar-auto --event ID --label L [--actividades id,id | --todo]
#               [--llegadas | --sin-llegadas] [--reemplazar-copia] [--reemplazar-nodo ID]
#   nodo-mac.sh subir-auto --event ID [--dry-run] [--prefer node|prod] [--final | --avance]
#   nodo-mac.sh nodo-info [--event ID]  alcance de ESTE nodo (local, sin red); se guarda para el panel
#   MODO MANUAL (respaldo, sin la API; conexión de producción en el Llavero):
#   nodo-mac.sh exportar [--dry-run] [--node-label L] [--activities id,id] [--check-in|--no-check-in]
#   nodo-mac.sh importar [--dry-run] [--prefer node|prod] [--keep-lock] [--partial]
#   nodo-mac.sh sync-estado             qué tiene cada lado
#   RED DEL EVENTO:
#   nodo-mac.sh red-modo glinet|google|otro|lugar   recuerda la opción elegida (glinet apaga el DNS de la Mac)
#   nodo-mac.sh router-plan|router-aplicar [--simular] [--tipo glinet|openwrt|mikrotik]
#                                       configura el router por SSH (uci en GL.iNet/OpenWrt, RouterOS en MikroTik)
#   nodo-mac.sh router-revisar          ¿el DNS del router dice que events.adventist.club es esta Mac?
#   nodo-mac.sh red-revisar             «Revisar» común: DNS vía el router, aislamiento, internet (+ @@json)
#   nodo-mac.sh red-diagnostico         «¿Puedo usar esta red?» con una recomendación (+ @@json)
#   nodo-mac.sh dns-on [--ip IP] | dns-off | dns-estado   DNS de esta Mac (para «Mi red»; puerto 53)
#   nodo-mac.sh red-publica-plan|red-publica-aplicar|red-publica-quitar [--simular] [--tipo glinet|openwrt|mikrotik]
#                                       «Red pública del evento»: sólo los nombres del evento, sin desvíos de DNS,
#                                       clientes aislados, respuesta de portal (RED_PORTAL) y sólo la Mac a internet
#   nodo-mac.sh cert-letsencrypt [--staging] | cert-local
#   nodo-mac.sh autoarranque on|off
#   nodo-mac.sh config json | config set CLAVE VALOR
#   nodo-mac.sh registros [servicio]    últimas líneas de los registros
#   nodo-mac.sh instalar                versión portátil (DMG): copia el nodo, las herramientas y
#                                       carga las imágenes que trae la app (SIN internet)
#
# Secretos: NUNCA se leen de disco ni se escriben. La app los saca del Llavero de macOS y los pasa
# por el entorno sólo a las órdenes que los necesitan (POSTGRES_PASSWORD, JWT_SECRET,
# EVENT_PASS_SECRET, PROXY_SHARED_SECRET, PROD_DATABASE_URL, PROD_EVENT_PASS_SECRET,
# CF_DNS_API_TOKEN, NODO_ROUTER_PASS, WIFI_PASSWORD, NODO_API_TOKEN). Este script jamás los imprime.
# Códigos de salida de las órdenes de sincronización: 0 ok · 1 error · 2 conflictos ·
# 4 hay que volver a iniciar sesión · 5 sin permiso / falta MFA.
#
# Líneas de progreso para la app: «@@paso N/M texto», «@@ok texto», «@@aviso texto», «@@error texto».
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export LC_ALL=en_US.UTF-8
# Sin «bake»: los Dockerfile del nodo viven fuera del contexto de build (bake pediría --allow).
export COMPOSE_BAKE=false
# Compose 2.3x+/5 construye con «bake», que exige permiso para leer los Dockerfile de fuera del
# contexto (nodo-evento/*.Dockerfile). Es nuestro propio repo: se permite.
export BUILDX_BAKE_ENTITLEMENTS_FS=0

APP_SUPPORT="${NODO_APP_SUPPORT:-$HOME/Library/Application Support/Nodo Camporee}"
CONFIG="$APP_SUPPORT/config.env"
VOLUMES_DIR="${NODO_VOLUMES_DIR:-/Volumes}"   # las pruebas pueden apuntar a otra carpeta
AGENT_PREFIX="${NODO_AGENT_PREFIX:-club.adventist.nodo-camporee}"   # las pruebas usan otro prefijo
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
RES_DIR="$(cd "$(dirname "$0")" && pwd)"   # Contents/Resources de la app (o mac/ del repo)
mkdir -p "$APP_SUPPORT" && chmod 700 "$APP_SUPPORT"

# Versión portátil (DMG, empaquetar.sh): la app trae dentro los archivos del nodo (nodo/), las
# imágenes de Docker (imagenes/*.tar), Colima + Lima + Docker (herramientas/) y la imagen de la
# máquina virtual. paquete.env (sin secretos) dice qué versión es. Sin él: instalación desde el repo.
PAQUETE=0
PAQUETE_VERSION=""; PAQUETE_ARCH=""; PAQUETE_BACKEND=""; PAQUETE_FRONTEND=""; PAQUETE_COLIMA=""
PAQUETE_VM_CACHE=""; PAQUETE_VM_FILE=""; PAQUETE_IMAGENES=""
# shellcheck disable=SC1091
[ -f "$RES_DIR/paquete.env" ] && . "$RES_DIR/paquete.env" && PAQUETE=1
HERR_DIR="$APP_SUPPORT/herramientas"   # copia de las herramientas de la app (rutas estables)

# ---------------------------------------------------------------------------------------------
# Configuración (sin secretos). Valores por defecto; config.env los sobreescribe.
NODO_DIR=""                 # copia de trabajo de nodo-evento/ (compose, Caddyfile, tls, datos): la
                            # pone instalar.sh en Application Support (fuera de Documentos)
BACKEND_DIR=""              # backend/ del repo (sólo para «Construir»)
FRONTEND_DIR=""             # repo del frontend (para construir la imagen web)
EVENTO_ID="10267b82-8260-4b27-ba41-de7924e3a9e5"   # Universo de Dios (producción)
ACTOR_EMAIL=""              # cuenta de coordinación que firma export/import
NODO_LABEL="Mac del nodo — Camporee"
EVENTO_FIN="2026-11-22"     # último día del evento (aviso si el certificado vence antes)
ROUTER_IP="192.168.8.1"     # GL.iNet Beryl AX (GL-MT3000): LAN por defecto
IP_FIJA=""                  # la IP que el router reserva para esta Mac (la pone «Configurar router»)
MAC_FIJA=""                 # la MAC registrada en el router
DOMINIOS="events.adventist.club api.adventist.club media.adventist.club"   # + admin.adventist.club si se quiere
WIFI_SSID=""
LE_EMAIL=""
MODO="auto"                 # auto (inicia sesión en adventist.club) | manual (conexión de producción)
RED_MODO=""                 # glinet | google | otro | lugar  (la opción de «Red del evento»)
ROUTER_TIPO="glinet"        # glinet | openwrt | mikrotik (configuración automática por SSH)
ROUTER_USUARIO=""           # usuario SSH del router (vacío: root en OpenWrt, admin en MikroTik)
RED_REVISION=""             # ok | falla  (último «Revisar»)
RED_REVISION_T=""           # cuándo (epoch)
RED_REVISION_TXT=""         # resumen corto
DNS_REENVIAR="1.1.1.1 8.8.8.8"   # DNS públicos a los que el DNS de la Mac reenvía lo que no es del evento
# «Red pública del evento» (GL.iNet/OpenWrt/MikroTik): Wi-Fi abierto o con clave sencilla, sólo los
# nombres del evento, sin desvíos de DNS, equipos aislados entre sí.
PUBLICA_SSID=""             # nombre del Wi-Fi (vacío = no cambiar)
PUBLICA_ABIERTA="1"         # 1 = sin contraseña · 0 = con WIFI_PASSWORD (Llavero)
RED_PORTAL="exito"          # ninguno | exito | bienvenida  (respuesta a «¿hay internet?» de los teléfonos)
PUBLICA_SOLO_MAC="1"        # 1 = sólo la Mac del nodo sale a internet (para «Subir avance»)
# Máquina de Docker PROPIA del nodo (no comparte disco ni contenedores con otros proyectos).
COLIMA_PROFILE="nodo"
# Recursos al crearla: la mitad de los núcleos (máx. 4) y 6 GB si la Mac tiene 16 GB o más.
_ncpu="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"; _ram_gb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 8589934592) / 1073741824 ))
COLIMA_CPU="$(( _ncpu / 2 > 4 ? 4 : (_ncpu / 2 < 2 ? 2 : _ncpu / 2) ))"
COLIMA_MEM="$( [ "$_ram_gb" -ge 16 ] && echo 6 || echo 4 )"
COLIMA_DISK="40"
RESPALDO_CADA_MIN="10"
# En la carpeta personal (no en Documentos/Escritorio): macOS no pide permisos para ella, y una
# pregunta de permisos sin contestar dejaría el nodo esperando.
RESPALDO_LOCAL="$HOME/Nodo Camporee/Respaldos"
USB_LABEL="NODO-RESPALDO"
WEB_BUILD_ID="nodo-local"
HERRAMIENTAS=""             # sistema (Homebrew u otro) | app (las que trae la app); lo fija «instalar»
ROUTER_IP_OTRO=""          # IP del router en «Otro router» (OpenWrt/MikroTik); vacío = la puerta de enlace
# «Puerta» (v3): la app escucha en :80/:443 y pasa a Caddy la IP REAL de cada teléfono (protocolo
# PROXY); Colima publica sólo en 127.0.0.1:PUERTO_INTERNO_*. PUERTA=0 = como antes (Colima publica
# 80/443 en toda la red y todos los teléfonos se ven con una sola IP).
PUERTA="1"
PUERTO_INTERNO_HTTP="8080"
PUERTO_INTERNO_HTTPS="8443"
# Actualizaciones (Sparkle): las imágenes de Docker se versionan aparte (sólo se descargan si cambian).
IMAGENES_URL="https://media.adventist.club/nodo/imagenes"
# shellcheck disable=SC1090
[ -f "$CONFIG" ] && . "$CONFIG"
# media.adventist.club (logos e imágenes copiados al preparar) siempre va al nodo, también con
# una configuración vieja guardada sin él.
case " $DOMINIOS " in *" media.adventist.club "*) ;; *) DOMINIOS="$DOMINIOS media.adventist.club" ;; esac
# Router «actual»: el GL.iNet (ROUTER_IP) o, con otra red (Google Wifi, otro router, red del lugar),
# la puerta de enlace de esta Mac.
GATEWAY="$(route -n get default 2>/dev/null | awk '/gateway:/{print $2; exit}')"
case "$RED_MODO" in
  ""|glinet) ;;
  otro) ROUTER_IP="${ROUTER_IP_OTRO:-${GATEWAY:-$ROUTER_IP}}" ;;
  publica) [ "$ROUTER_TIPO" = glinet ] || ROUTER_IP="${ROUTER_IP_OTRO:-${GATEWAY:-$ROUTER_IP}}" ;;
  *) ROUTER_IP="${GATEWAY:-$ROUTER_IP}" ;;
esac
RUN_DIR="$APP_SUPPORT/run"; mkdir -p "$RUN_DIR"

# Herramientas: las del sistema si están completas; si no, las que trae la app (copiadas por
# «instalar» a Application Support: así no dependen de dónde esté la app ni del DMG montado).
sistema_completo() {
  command -v colima >/dev/null 2>&1 && command -v limactl >/dev/null 2>&1 \
    && command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1
}
usar_herramientas_app() {
  export PATH="$HERR_DIR/bin:$PATH"
  # Configuración de Docker propia: el plugin compose de la app y sus contextos, sin tocar ~/.docker.
  export DOCKER_CONFIG="$APP_SUPPORT/docker"
}
HERR_EN_USO="faltan"
case "${NODO_HERRAMIENTAS:-$HERRAMIENTAS}" in
  app) [ -x "$HERR_DIR/bin/colima" ] && { usar_herramientas_app; HERR_EN_USO=app; } ;;
  sistema) sistema_completo && HERR_EN_USO=sistema ;;
  *) if sistema_completo; then HERR_EN_USO=sistema
     elif [ -x "$HERR_DIR/bin/colima" ]; then usar_herramientas_app; HERR_EN_USO=app; fi ;;
esac

DOCKER_SOCK="$HOME/.colima/${COLIMA_PROFILE}/docker.sock"
export DOCKER_HOST="unix://$DOCKER_SOCK"
# Sólo pruebas: otro proyecto de compose (otros contenedores) y un compose extra encima.
PROJECT="${NODO_COMPOSE_PROJECT:-nodo-evento}"

say()   { printf '%s\n' "$*"; }
paso()  { printf '@@paso %s\n' "$*"; }
ok()    { printf '@@ok %s\n' "$*"; }
aviso() { printf '@@aviso %s\n' "$*"; }
fallo() { printf '@@error %s\n' "$*"; exit 1; }

# Depurador (depurar.pl junto a este script; docs/EGRESO.md): lo que viene del router, de Docker
# (registros de la API/Caddy) o de órdenes de la Mac pasa por aquí antes de mostrarse. La app lo
# vuelve a depurar (app/Depurador.swift). Sin el script no se muestra nada en vez de mostrarlo crudo.
DEPURAR_PL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/depurar.pl"
depurar() {
  if [ -f "$DEPURAR_PL" ]; then perl "$DEPURAR_PL" "$@"
  else cat >/dev/null; printf '(salida oculta: falta depurar.pl junto a nodo-mac.sh)\n'; fi
}

json_str() {  # escapa para JSON (sin jq: el estado se pide cada pocos segundos)
  local s="${1-}"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; s="${s//$'\r'/}"; s="${s//$'\t'/ }"
  printf '"%s"' "$s"
}
jb() { [ "${1:-0}" = 1 ] && printf true || printf false; }

config_set() {
  local key="$1" value="$2" tmp
  case "$key" in
    NODO_DIR|FRONTEND_DIR|BACKEND_DIR|EVENTO_ID|ACTOR_EMAIL|NODO_LABEL|EVENTO_FIN|ROUTER_IP|IP_FIJA|MAC_FIJA|DOMINIOS|WIFI_SSID|LE_EMAIL|COLIMA_PROFILE|COLIMA_CPU|COLIMA_MEM|COLIMA_DISK|RESPALDO_CADA_MIN|RESPALDO_LOCAL|USB_LABEL|WEB_BUILD_ID|HERRAMIENTAS) ;;
    PUBLICA_SSID|PUBLICA_ABIERTA|RED_PORTAL|PUBLICA_SOLO_MAC) ;;
    MODO|RED_MODO|ROUTER_TIPO|ROUTER_USUARIO|ROUTER_IP_OTRO|RED_REVISION|RED_REVISION_T|RED_REVISION_TXT|DNS_REENVIAR) ;;
    *) fallo "clave de configuración desconocida: $key" ;;
  esac
  tmp="$(mktemp "$APP_SUPPORT/.config.XXXXXX")"
  { [ -f "$CONFIG" ] && grep -v "^${key}=" "$CONFIG"; printf '%s=%q\n' "$key" "$value"; } > "$tmp"
  chmod 600 "$tmp" && mv "$tmp" "$CONFIG"
}

config_json() {
  printf '{'
  local first=1 k
  for k in NODO_DIR FRONTEND_DIR EVENTO_ID ACTOR_EMAIL NODO_LABEL EVENTO_FIN ROUTER_IP IP_FIJA MAC_FIJA DOMINIOS WIFI_SSID LE_EMAIL COLIMA_PROFILE COLIMA_CPU COLIMA_MEM RESPALDO_CADA_MIN RESPALDO_LOCAL USB_LABEL MODO RED_MODO ROUTER_TIPO ROUTER_USUARIO ROUTER_IP_OTRO GATEWAY PUBLICA_SSID PUBLICA_ABIERTA RED_PORTAL PUBLICA_SOLO_MAC RED_REVISION RED_REVISION_T RED_REVISION_TXT; do
    [ $first = 1 ] || printf ','; first=0
    printf '"%s":%s' "$k" "$(json_str "${!k}")"
  done
  printf '}'
}

# ---------------------------------------------------------------------------------------------
# Docker / Colima
docker_ok() { docker info >/dev/null 2>&1; }

colima_running() { colima status --profile "$COLIMA_PROFILE" >/dev/null 2>&1; }

start_colima() {
  if colima_running && docker_ok; then ok "Docker (Colima) ya estaba encendido"; return 0; fi
  if ! command -v colima >/dev/null; then
    [ "$PAQUETE" = 1 ] && fallo "Faltan Colima y Docker: pulsa «Instalar» (vienen dentro de la app)."
    fallo "Falta Colima. Instálalo una vez con internet: brew install colima docker docker-compose"
  fi
  # colima cambia el «contexto» global de docker al perfil que arranca: se devuelve el anterior
  # para no alterar otros proyectos de esta Mac (el nodo usa DOCKER_HOST explícito).
  local prev_ctx; prev_ctx="$(DOCKER_HOST= docker context show 2>/dev/null || true)"
  if colima list -j 2>/dev/null | grep -q "\"name\":\"${COLIMA_PROFILE}\""; then
    say "Encendiendo Docker (Colima, perfil ${COLIMA_PROFILE})…"
    colima start --profile "$COLIMA_PROFILE" 2>&1 | sed 's/^/  /' | tail -5
  else
    say "Creando la máquina de Docker: ${COLIMA_CPU} CPU, ${COLIMA_MEM} GB, ${COLIMA_DISK} GB de disco…"
    sembrar_imagen_vm
    # NODO_COLIMA_EXTRA: sólo pruebas (p. ej. «--dns 192.0.2.1» para una máquina sin internet).
    # shellcheck disable=SC2086
    colima start --profile "$COLIMA_PROFILE" --cpu "$COLIMA_CPU" --memory "$COLIMA_MEM" --disk "$COLIMA_DISK" \
      --vm-type vz --mount-type virtiofs ${NODO_COLIMA_EXTRA:-} 2>&1 | sed 's/^/  /' | tail -5
  fi
  [ -n "$prev_ctx" ] && DOCKER_HOST= docker context use "$prev_ctx" >/dev/null 2>&1 || true
  local i; for i in $(seq 1 60); do docker_ok && break; sleep 2; done
  docker_ok || fallo "Docker no respondió. Prueba de nuevo; si sigue, reinicia la Mac."
  ok "Docker (Colima) encendido"
}

compose_env_file() {
  local f="$RUN_DIR/nodo.env"
  {
    printf 'BACKEND_DIR=%s\n' "$BACKEND_DIR"
    printf 'FRONTEND_DIR=%s\n' "$FRONTEND_DIR"
    printf 'NODO_DIR=%s\n' "$NODO_DIR"
    printf 'NODO_DIR_ABS=%s\n' "$NODO_DIR"
    printf 'DATOS_DIR=%s\n' "$NODO_DIR/datos"
    printf 'RESPALDO_DIR=%s\n' "$RESPALDO_LOCAL"
    printf 'RESPALDO_CADA_MIN=%s\n' "$RESPALDO_CADA_MIN"
    printf 'RESPALDO_CONSERVAR=%s\n' 300
    printf 'NODO_IP=\n'
    if puerta_activa; then
      printf 'NODO_PUBLICAR=127.0.0.1\nNODO_PUERTO_HTTP=%s\nNODO_PUERTO_HTTPS=%s\n' "$PUERTO_INTERNO_HTTP" "$PUERTO_INTERNO_HTTPS"
      # Sólo la puerta llega a Caddy (127.0.0.1 → reenviador de Colima → red de Docker): sólo de ahí
      # se cree la línea PROXY con la IP real.
      printf 'NODO_PROXY_PERMITIDOS="127.0.0.0/8 ::1/128 10.0.0.0/8 172.16.0.0/12"\n'
    else
      printf 'NODO_PUBLICAR=0.0.0.0\nNODO_PUERTO_HTTP=80\nNODO_PUERTO_HTTPS=443\n'
    fi
    # Carpeta compartida con la app: vecinos (Bonjour) y estado de la malla.
    printf 'NODO_RUN_DIR=%s\n' "$RUN_DIR"
    printf 'NODO_HOSTS="events.adventist.club, admin.adventist.club, api.adventist.club, media.adventist.club"\n'
    printf 'WEB_BUILD_ID=%s\n' "$WEB_BUILD_ID"
    # Las respuestas de portal sólo valen con la red pública (si no, esos nombres ni llegan al nodo).
    printf 'NODO_PORTAL=%s\n' "$( [ "$RED_MODO" = publica ] && printf '%s' "${RED_PORTAL:-exito}" || printf ninguno )"
  } > "$f"
  chmod 600 "$f"
  printf '%s' "$f"
}

# docker compose con los secretos que la app pasó por el entorno. Las órdenes que no los usan
# (down, ps) reciben un relleno: compose exige las variables para interpolar, aunque no las use.
# Nombres de esta Mac (equipo, nombre local, usuario, nombre completo): sólo por el entorno, a los
# contenedores sync y malla, para que quiten esos nombres de lo que sube a la nube o a los vecinos
# (backend/app/services/nodo_egreso.py). Nunca se escriben en un archivo.
equipo_nombres() {
  local n out=""
  for n in "$(scutil --get ComputerName 2>/dev/null)" "$(scutil --get LocalHostName 2>/dev/null)" \
           "$(hostname -s 2>/dev/null)" "$(id -un 2>/dev/null)" "$(id -F 2>/dev/null)"; do
    [ "${#n}" -ge 3 ] && out="${out:+$out|}$n"
  done
  printf '%s' "$out"
}

dc() {
  [ -n "${NODO_EQUIPO_NOMBRES+x}" ] || { NODO_EQUIPO_NOMBRES="$(equipo_nombres)"; export NODO_EQUIPO_NOMBRES; }
  local envf; envf="$(compose_env_file)"
  local extra=(); [ -n "${NODO_COMPOSE_EXTRA:-}" ] && extra=(-f "$NODO_COMPOSE_EXTRA")
  docker compose -p "$PROJECT" --project-directory "$NODO_DIR" -f "$NODO_DIR/docker-compose.yml" ${extra[@]+"${extra[@]}"} --env-file "$envf" "$@"
}
dc_nosecrets() {
  POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-x}" JWT_SECRET="${JWT_SECRET:-x}" \
  EVENT_PASS_SECRET="${EVENT_PASS_SECRET:-x}" PROXY_SHARED_SECRET="${PROXY_SHARED_SECRET:-x}" dc "$@"
}

need_node_dir() {
  [ -n "$NODO_DIR" ] && [ -f "$NODO_DIR/docker-compose.yml" ] && return 0
  [ "$PAQUETE" = 1 ] && fallo "Falta instalar el nodo en esta Mac: pulsa «Instalar»."
  fallo "No encuentro la carpeta del nodo (nodo-evento/). Vuelve a correr instalar.sh."
}

need_secrets() {
  local missing=() v
  for v in POSTGRES_PASSWORD JWT_SECRET PROXY_SHARED_SECRET EVENT_PASS_SECRET; do
    [ -n "${!v:-}" ] || missing+=("$v")
  done
  [ ${#missing[@]} = 0 ] || fallo "Faltan secretos: ${missing[*]}. Abre «Ajustes › Secretos» en la app."
}

container() { printf '%s-%s-1' "$PROJECT" "$1"; }

service_state() {  # running|healthy|unhealthy|starting|exited|absent
  local st
  st="$(docker inspect -f '{{.State.Status}}{{if .State.Health}}/{{.State.Health.Status}}{{end}}' "$(container "$1")" 2>/dev/null)" || { printf absent; return; }
  case "$st" in
    running/healthy) printf healthy ;;
    running/unhealthy) printf unhealthy ;;
    running/starting) printf starting ;;
    running) printf running ;;
    *) printf '%s' "${st%%/*}" ;;
  esac
}

# ---------------------------------------------------------------------------------------------
# Red
iface_kind() {  # en0 -> wifi|ethernet|otro
  networksetup -listallhardwareports 2>/dev/null | awk -v d="$1" '
    /^Hardware Port:/ { port=substr($0, 16) }
    /^Device:/ && $2==d { if (port ~ /Wi-Fi|AirPort/) print "wifi"; else if (port ~ /Ethernet|LAN|Thunderbolt Ethernet|USB/) print "ethernet"; else print "otro"; exit }'
}

router_prefix() { printf '%s' "${ROUTER_IP%.*}."; }

# Elige la interfaz: la que esté en la red del router (Ethernet primero), si no la de la ruta por defecto.
detect_net() {
  NET_IFACE=""; NET_IP=""; NET_KIND=""; NET_MAC=""; NET_EN_ROUTER=0
  local pfx ifc ip kind best="" bestip="" bestkind=""
  pfx="$(router_prefix)"
  for ifc in $(ifconfig -l 2>/dev/null); do
    case "$ifc" in lo*|gif*|stf*|utun*|awdl*|llw*|anpi*|ap*|bridge*|vmenet*|feth*) continue ;; esac
    ip="$(ipconfig getifaddr "$ifc" 2>/dev/null)" || continue
    [ -n "$ip" ] || continue
    if [ "${ip#"$pfx"}" != "$ip" ]; then
      kind="$(iface_kind "$ifc")"
      if [ -z "$best" ] || { [ "$kind" = ethernet ] && [ "$bestkind" != ethernet ]; }; then
        best="$ifc"; bestip="$ip"; bestkind="$kind"
      fi
    fi
  done
  if [ -n "$best" ]; then
    NET_IFACE="$best"; NET_IP="$bestip"; NET_KIND="$bestkind"; NET_EN_ROUTER=1
  else
    NET_IFACE="$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')"
    [ -n "$NET_IFACE" ] && NET_IP="$(ipconfig getifaddr "$NET_IFACE" 2>/dev/null)"
    [ -n "$NET_IFACE" ] && NET_KIND="$(iface_kind "$NET_IFACE")"
  fi
  [ -n "$NET_IFACE" ] && NET_MAC="$(ifconfig "$NET_IFACE" 2>/dev/null | awk '/ether /{print $2; exit}')"
  return 0
}

router_alive() { ping -c 1 -t 1 "$ROUTER_IP" >/dev/null 2>&1; }

router_is_glinet() {
  local body
  body="$(curl -sk --max-time 2 "http://$ROUTER_IP/" 2>/dev/null; curl -sk --max-time 2 "https://$ROUTER_IP/" 2>/dev/null)"
  printf '%s' "$body" | grep -qiE 'gl\.inet|glinet|gl-mt|gl-ui'
}

# ---------------------------------------------------------------------------------------------
# Salud por HTTPS (siempre contra esta Mac; --resolve evita depender del DNS)
https_get() {  # https_get host path ip [puerto]
  local ca=() port="${4:-443}"
  [ -s "$NODO_DIR/tls/actual/ca-local.pem" ] && ca=(--cacert "$NODO_DIR/tls/actual/ca-local.pem")
  curl -fsS -o /dev/null --max-time 4 ${ca[@]+"${ca[@]}"} --resolve "$1:$port:$3" "https://$1:$port$2" 2>/dev/null
}
# Caddy por dentro (sin pasar por la puerta): con la puerta, 127.0.0.1:PUERTO_INTERNO_HTTPS.
https_local() { if puerta_activa; then https_get "$1" "$2" 127.0.0.1 "$PUERTO_INTERNO_HTTPS"; else https_get "$1" "$2" 127.0.0.1; fi; }

# ---------------------------------------------------------------------------------------------
# Certificado
cert_info() {  # CERT_EXISTE CERT_TIPO CERT_VENCE CERT_DIAS CERT_NOMBRES CERT_VALIDO_EVENTO
  CERT_EXISTE=0; CERT_TIPO=""; CERT_VENCE=""; CERT_DIAS=""; CERT_NOMBRES=""; CERT_CUBRE_EVENTO=0
  local f="$NODO_DIR/tls/actual/fullchain.pem" end issuer end_epoch now fin_epoch
  [ -s "$f" ] || return 0
  CERT_EXISTE=1
  issuer="$(openssl x509 -noout -issuer -in "$f" 2>/dev/null)"
  if [ -s "$NODO_DIR/tls/actual/ca-local.pem" ] || printf '%s' "$issuer" | grep -q 'Nodo evento CA local'; then
    CERT_TIPO="ca-local"
  elif printf '%s' "$issuer" | grep -qiE "STAGING|Fake|\(STAGING\)"; then
    CERT_TIPO="letsencrypt-ensayo"
  elif printf '%s' "$issuer" | grep -qiE "Let's Encrypt|O ?= ?Let"; then
    CERT_TIPO="letsencrypt"
  else
    CERT_TIPO="otro"
  fi
  end="$(openssl x509 -noout -enddate -in "$f" 2>/dev/null | cut -d= -f2)"
  end_epoch="$(date -j -f '%b %e %T %Y %Z' "$end" +%s 2>/dev/null || echo 0)"
  now="$(date +%s)"
  CERT_VENCE="$(date -r "$end_epoch" '+%Y-%m-%d' 2>/dev/null)"
  CERT_DIAS=$(( (end_epoch - now) / 86400 ))
  fin_epoch="$(date -j -f '%Y-%m-%d %H:%M:%S' "$EVENTO_FIN 23:59:59" +%s 2>/dev/null || echo 0)"
  [ "$end_epoch" -gt "$fin_epoch" ] && CERT_CUBRE_EVENTO=1
  CERT_NOMBRES="$(openssl x509 -noout -ext subjectAltName -in "$f" 2>/dev/null | tail -n +2 | tr -d ' ' | tr ',' ' ')"
}

# ---------------------------------------------------------------------------------------------
# Respaldos
usb_dir() {  # imprime la carpeta de respaldos en la USB, si está conectada y se puede escribir
  local v="$VOLUMES_DIR/$USB_LABEL"
  [ -d "$v" ] && [ -w "$v" ] || return 1
  mkdir -p "$v/nodo-camporee" 2>/dev/null || return 1
  printf '%s' "$v/nodo-camporee"
}

last_dump() {  # último respaldo en una carpeta: "ruta|epoch"
  local d="$1" f
  f="$(ls -1 "$d"/nodo-*.dump 2>/dev/null | sort | tail -1)"
  [ -n "$f" ] || return 1
  printf '%s|%s' "$f" "$(stat -f %m "$f")"
}

cmd_respaldo_espejo() {
  local usb
  if ! usb="$(usb_dir)"; then say "USB «${USB_LABEL}» no conectada: los respaldos siguen en $RESPALDO_LOCAL"; return 0; fi
  [ -d "$RESPALDO_LOCAL" ] || { say "aún no hay respaldos locales"; return 0; }
  # Sólo los .dump terminados (el temporal empieza con punto). Sin --delete: la USB guarda de más.
  if rsync -a --include 'nodo-*.dump' --include 'ultimo.dump' --exclude '*' "$RESPALDO_LOCAL/" "$usb/" 2>&1; then
    date '+%Y-%m-%d %H:%M:%S' > "$RUN_DIR/ultimo-espejo"
    ok "Respaldos copiados a la USB ($usb)"
  else
    aviso "No se pudo copiar a la USB ($usb)"
  fi
}

cmd_respaldar() {
  need_node_dir
  [ "$(service_state db)" = healthy ] || fallo "El nodo está apagado: no hay base que respaldar."
  local c; c="$(container respaldo)"
  docker inspect "$c" >/dev/null 2>&1 || fallo "El servicio de respaldo no está corriendo."
  say "Respaldo ahora…"
  docker exec "$c" sh -c 'f=/respaldos/nodo-$(date +%Y%m%d-%H%M%S)-manual.dump; pg_dump -Fc -Z 6 -f "/respaldos/.manual.tmp" && pg_restore -l /respaldos/.manual.tmp >/dev/null && mv /respaldos/.manual.tmp "$f" && cp "$f" /respaldos/.ultimo.tmp && mv /respaldos/.ultimo.tmp /respaldos/ultimo.dump && echo "ok $(basename "$f")"' \
    || fallo "El respaldo falló (revisa «Registros › respaldo»)"
  ok "Respaldo guardado en $RESPALDO_LOCAL"
  cmd_respaldo_espejo
}

# ---------------------------------------------------------------------------------------------
# LaunchAgents: despierto (caffeinate), espejo de respaldos y auto-arranque
APP_BUNDLE="${NODO_APP_BUNDLE:-$HOME/Applications/Nodo Camporee.app}"
APP_EXE="$APP_BUNDLE/Contents/MacOS/Nodo Camporee"
LA_DIR="$HOME/Library/LaunchAgents"

agent_plist() { printf '%s/%s.%s.plist' "$LA_DIR" "$AGENT_PREFIX" "$1"; }
agent_loaded() { launchctl print "gui/$(id -u)/$AGENT_PREFIX.$1" >/dev/null 2>&1; }
agent_load() {
  launchctl bootout "gui/$(id -u)/$AGENT_PREFIX.$1" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$(agent_plist "$1")"
}
agent_unload() {
  launchctl bootout "gui/$(id -u)/$AGENT_PREFIX.$1" >/dev/null 2>&1 || true
  rm -f "$(agent_plist "$1")"
}

write_plist() {  # write_plist nombre <<< cuerpo (dentro de <dict>)
  mkdir -p "$LA_DIR"
  {
    printf '<?xml version="1.0" encoding="UTF-8"?>\n'
    printf '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    printf '<plist version="1.0"><dict>\n<key>Label</key><string>%s.%s</string>\n' "$AGENT_PREFIX" "$1"
    # La carpeta de datos y el prefijo viajan al agente (las pruebas usan otros, aparte del real).
    printf '<key>EnvironmentVariables</key><dict><key>NODO_APP_SUPPORT</key><string>%s</string><key>NODO_AGENT_PREFIX</key><string>%s</string></dict>\n' "$(xml "$APP_SUPPORT")" "$(xml "$AGENT_PREFIX")"
    cat
    printf '</dict></plist>\n'
  } > "$(agent_plist "$1")"
}

xml() { local s="$1"; s="${s//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"; printf '%s' "$s"; }

start_awake() {
  # -i: sin reposo por inactividad · -m: disco despierto · -s: sin reposo con cargador.
  # La pantalla SÍ puede apagarse (ahorra batería). La tapa debe quedar ABIERTA.
  write_plist despierto <<EOF
<key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-ims</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
EOF
  agent_load despierto && ok "La Mac no se dormirá mientras el nodo esté encendido"
}

start_mirror() {
  local secs=$(( RESPALDO_CADA_MIN * 60 )); [ "$secs" -ge 120 ] || secs=120
  # Lo corre el ejecutable de la app (así macOS pide UNA vez permiso para la USB a «Nodo Camporee»).
  local prog
  if [ -x "$APP_EXE" ]; then
    prog="<string>$(xml "$APP_EXE")</string><string>--motor</string><string>respaldo-espejo</string>"
  else
    prog="<string>/bin/bash</string><string>$(xml "$SELF")</string><string>respaldo-espejo</string>"
  fi
  write_plist respaldo-usb <<EOF
<key>ProgramArguments</key><array>$prog</array>
<key>StartInterval</key><integer>$secs</integer><key>RunAtLoad</key><false/>
<key>StandardOutPath</key><string>$(xml "$RUN_DIR/respaldo-usb.log")</string>
<key>StandardErrorPath</key><string>$(xml "$RUN_DIR/respaldo-usb.log")</string>
EOF
  agent_load respaldo-usb && ok "Copia a la USB cada $((secs / 60)) min (si está conectada)"
}

cmd_autoarranque() {
  case "${1:-}" in
    on)
      [ -d "$APP_BUNDLE" ] || fallo "No encuentro la app en $APP_BUNDLE"
      write_plist autoarranque <<EOF
<key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-a</string><string>$(xml "$APP_BUNDLE")</string><string>--args</string><string>--encender</string></array>
<key>RunAtLoad</key><true/>
EOF
      # No se carga ahora (abriría la app): launchd la lee al iniciar sesión.
      ok "La app abrirá y encenderá el nodo al iniciar sesión en esta Mac" ;;
    off) agent_unload autoarranque; ok "Auto-arranque desactivado" ;;
    *) fallo "uso: autoarranque on|off" ;;
  esac
}

# ---------------------------------------------------------------------------------------------
# Versión portátil: «Instalar» (sin internet, sin los repos)
imagenes_completas() {  # ¿están todas las imágenes que necesita el nodo?
  local lista="${PAQUETE_IMAGENES:-nodo-evento-api:local nodo-evento-web:local postgres:16-alpine caddy:2.10-alpine}"
  # shellcheck disable=SC2086
  docker image inspect $lista >/dev/null 2>&1
}

instalacion_pendiente() {  # imprime el motivo y devuelve 0 si falta «Instalar»
  [ "$PAQUETE" = 1 ] || return 1
  if [ -z "$NODO_DIR" ] || [ ! -f "$NODO_DIR/docker-compose.yml" ]; then printf 'primera vez'; return 0; fi
  if [ "$HERR_EN_USO" = faltan ]; then printf 'faltan Colima y Docker'; return 0; fi
  local inst=""; [ -f "$APP_SUPPORT/instalado" ] && inst="$(cat "$APP_SUPPORT/instalado")"
  if [ "$inst" != "$PAQUETE_VERSION" ]; then printf 'versión nueva de la app'; return 0; fi
  return 1
}

sembrar_imagen_vm() {
  # Colima descarga la imagen de su máquina virtual la primera vez. La app trae esa misma imagen:
  # se deja en la caché de Colima con el nombre que Colima busca y así no hace falta internet.
  [ "$PAQUETE" = 1 ] && [ -n "$PAQUETE_VM_CACHE" ] || return 0
  local src="$RES_DIR/herramientas/vm/$PAQUETE_VM_FILE" cache="$HOME/Library/Caches/colima/caches"
  [ -s "$src" ] || return 0
  local ver; ver="$(colima version 2>/dev/null | awk '/^colima version/{print $3}')"
  if [ -n "$PAQUETE_COLIMA" ] && [ "$ver" != "$PAQUETE_COLIMA" ]; then
    aviso "Colima $ver no es la versión de la app ($PAQUETE_COLIMA): la primera vez puede necesitar internet"
    return 0
  fi
  [ -s "$cache/$PAQUETE_VM_CACHE" ] && return 0
  if mkdir -p "$cache" && cp "$src" "$cache/.$PAQUETE_VM_CACHE.tmp" && mv "$cache/.$PAQUETE_VM_CACHE.tmp" "$cache/$PAQUETE_VM_CACHE"; then
    say "Imagen de la máquina virtual lista (copiada de la app, sin descargar)"
  else
    aviso "No pude copiar la imagen de la máquina virtual"
  fi
}

cargar_imagenes() {  # docker load de las imágenes que trae la app (local: no usa internet)
  # v3: con manifiesto (imagenes.json) sólo se carga lo que cambió; la app «ligera» de una
  # actualización no trae los archivos y los toma de la caché o de internet.
  if [ -s "$RES_DIR/imagenes.json" ]; then cmd_imagenes_actualizar; return; fi
  local d="$RES_DIR/imagenes" f out n=0
  [ -d "$d" ] || fallo "Esta app no trae las imágenes del sistema."
  if [ "$(cat "$RUN_DIR/imagenes-cargadas" 2>/dev/null)" = "$PAQUETE_VERSION" ] && imagenes_completas; then
    ok "Las imágenes de esta versión ya estaban cargadas"; return 0
  fi
  for f in "$d"/*.tar; do
    [ -f "$f" ] || continue
    say "Cargando $(basename "$f" .tar) ($(du -h "$f" | cut -f1 | tr -d ' '))…"
    out="$(docker load -i "$f" 2>&1)" || { printf '%s\n' "$out" | tail -5; fallo "No se pudo cargar $(basename "$f")"; }
    printf '%s\n' "$out" | grep -E '^Loaded image' | sed 's/^/  /'
    n=$((n + 1))
  done
  imagenes_completas || fallo "Después de cargar faltan imágenes (${PAQUETE_IMAGENES})"
  printf '%s' "$PAQUETE_VERSION" > "$RUN_DIR/imagenes-cargadas"
  ok "Sistema cargado ($n archivos; web ${PAQUETE_FRONTEND:-?} · API ${PAQUETE_BACKEND:-?})"
}

cmd_instalar() {
  [ "$PAQUETE" = 1 ] || fallo "Esta app no trae el paquete portátil (se instaló desde el repo con instalar.sh)."
  if [ "$(uname -m)" != "${PAQUETE_ARCH:-arm64}" ]; then
    fallo "Esta app es para Macs ${PAQUETE_ARCH:-arm64} y esta Mac es $(uname -m) (ver README: Macs Intel)."
  fi
  local total=5 runtime="$APP_SUPPORT/nodo"
  paso "1/$total Copiando los archivos del nodo"
  # ditto mezcla: actualiza compose, Caddyfile y scripts; NO toca datos/, tls/actual ni tls/letsencrypt.
  mkdir -p "$runtime" && ditto "$RES_DIR/nodo" "$runtime" || fallo "No pude copiar los archivos del nodo a $runtime"
  chmod 755 "$runtime/nodo.sh" "$runtime/respaldo/respaldo.sh" "$runtime"/tls/*.sh 2>/dev/null
  config_set NODO_DIR "$runtime"; NODO_DIR="$runtime"
  ok "Archivos del nodo en $runtime"

  paso "2/$total Herramientas (Colima y Docker)"
  if [ "${NODO_HERRAMIENTAS:-$HERRAMIENTAS}" != app ] && sistema_completo; then
    config_set HERRAMIENTAS sistema; HERR_EN_USO=sistema
    ok "Se usan Colima y Docker que ya tiene esta Mac (Colima $(colima version 2>/dev/null | awk '/^colima version/{print $3}'))"
  else
    if [ ! -x "$RES_DIR/herramientas/bin/colima" ] && [ -x "$HERR_DIR/bin/colima" ]; then
      # Actualización «ligera» (sin herramientas dentro): se conservan las que ya se instalaron.
      config_set HERRAMIENTAS app; HERR_EN_USO=app; usar_herramientas_app
      ok "Se conservan Colima y Docker ya instalados (Colima $(colima version 2>/dev/null | awk '/^colima version/{print $3}'))"
    else
    [ -x "$RES_DIR/herramientas/bin/colima" ] || fallo "La app no trae Colima/Docker y esta Mac no los tiene. Usa el DMG completo (o con internet: brew install colima docker docker-compose)"
    rm -rf "$HERR_DIR.nuevo" && mkdir -p "$HERR_DIR.nuevo" \
      && ditto "$RES_DIR/herramientas/bin" "$HERR_DIR.nuevo/bin" \
      && ditto "$RES_DIR/herramientas/share" "$HERR_DIR.nuevo/share" \
      && ditto "$RES_DIR/herramientas/licencias" "$HERR_DIR.nuevo/licencias" \
      || fallo "No pude copiar las herramientas a $HERR_DIR"
    # Copiadas por la app (que macOS ya verificó al abrirla): sin la marca de «descargado».
    xattr -dr com.apple.quarantine "$HERR_DIR.nuevo" 2>/dev/null || true
    rm -rf "$HERR_DIR" && mv "$HERR_DIR.nuevo" "$HERR_DIR"
    mkdir -p "$APP_SUPPORT/docker/cli-plugins"
    ln -sfn "$HERR_DIR/bin/docker-compose" "$APP_SUPPORT/docker/cli-plugins/docker-compose"
    [ -f "$APP_SUPPORT/docker/config.json" ] || printf '{}\n' > "$APP_SUPPORT/docker/config.json"
    config_set HERRAMIENTAS app; HERR_EN_USO=app
    usar_herramientas_app
    docker compose version >/dev/null 2>&1 || fallo "Docker Compose de la app no responde"
    ok "Colima $(colima version 2>/dev/null | awk '/^colima version/{print $3}') y Docker de la app listos (sin Homebrew)"
    fi
  fi

  paso "3/$total Encendiendo Docker (la primera vez crea la máquina virtual: 1–3 min)"
  start_colima

  paso "4/$total Cargando el sistema (sin internet si viene en la app o ya estaba; 1–3 min)"
  cargar_imagenes

  paso "5/$total Terminando"
  mkdir -p "$RESPALDO_LOCAL" 2>/dev/null || true
  printf '%s' "$PAQUETE_VERSION" > "$APP_SUPPORT/instalado"
  ok "Instalado (versión $PAQUETE_VERSION). Siguiente: Secretos, certificado y «Encender»."
}

# ---------------------------------------------------------------------------------------------
cmd_encender() {
  local motivo; motivo="$(instalacion_pendiente)" && fallo "Falta instalar ($motivo): pulsa «Instalar»."
  need_node_dir
  need_secrets
  local total=7 free_gb
  paso "1/$total Revisando la Mac"
  free_gb="$(df -g "$HOME" | awk 'NR==2{print $4}')"
  [ "${free_gb:-0}" -ge 5 ] || fallo "Sólo quedan ${free_gb} GB libres. Libera al menos 10 GB antes de encender."
  [ "${free_gb:-0}" -ge 15 ] || aviso "Quedan ${free_gb} GB libres en el disco (recomendado: 15 GB o más)"
  pmset -g batt 2>/dev/null | grep -q "Battery Power" && aviso "La Mac está con BATERÍA: conecta el cargador"
  paso "2/$total Encendiendo Docker"
  start_colima
  paso "3/$total Revisando el sistema instalado"
  if ! docker image inspect "nodo-evento-api:${NODO_TAG:-local}" "nodo-evento-web:${NODO_TAG:-local}" >/dev/null 2>&1; then
    # Versión portátil: las imágenes vienen en la app (p. ej. si se borró la máquina de Docker).
    if [ "$PAQUETE" = 1 ]; then cargar_imagenes
    else fallo "Falta construir el sistema (Ajustes › «Construir», con internet, una sola vez)."; fi
  elif [ "$PAQUETE" = 1 ] && [ -s "$RES_DIR/imagenes.json" ] && ! cmd_imagenes_estado | grep -q '"faltan":0'; then
    # Una actualización de la app pide imágenes nuevas: se cargan si se pueden conseguir; si no
    # (sin internet y sin la caché), se sigue con las anteriores y se avisa.
    ( cmd_imagenes_actualizar ) || aviso "Faltan las imágenes nuevas de esta versión: sigo con las anteriores. Con internet: Ajustes › Actualizaciones › «Descargar imágenes» (o instala el DMG completo)."
  fi
  if ! { [ -s "$NODO_DIR/tls/actual/fullchain.pem" ] && [ -s "$NODO_DIR/tls/actual/privkey.pem" ]; }; then
    # Modo automático: el certificado viene al «Preparar»; si aún no hay, uno local para poder
    # empezar (plan B). En modo manual se pide explícitamente (Let's Encrypt o local).
    [ "$MODO" = manual ] && fallo "Falta el certificado HTTPS (botón «Certificado»)."
    aviso "Todavía no hay certificado: creo uno local (plan B). «Preparar» trae el bueno si producción lo tiene."
    cert_local_crear || fallo "No se pudo crear el certificado local"
  fi
  mkdir -p "$NODO_DIR/datos/media" "$NODO_DIR/datos/exports" "$RESPALDO_LOCAL" || fallo "No puedo crear $RESPALDO_LOCAL"
  ok "Sistema listo"
  paso "4/$total Arrancando base de datos, API, sitio y respaldos (1–2 min)"
  local out
  preparar_bloqueos
  local servicios="db migrate api web caddy respaldo"
  # Nodos vecinos (v3): el servicio «malla» si el compose lo trae.
  # Sólo si la imagen ya trae scripts/nodo_malla.py (con imágenes viejas, sin «Construir», se omite).
  if dc_nosecrets config --services 2>/dev/null | grep -qx malla \
     && docker run --rm --entrypoint test "nodo-evento-api:${NODO_TAG:-local}" -f scripts/nodo_malla.py >/dev/null 2>&1; then
    servicios="$servicios malla"
  else
    aviso "Nodos vecinos: el sistema instalado aún no lo trae (falta «Construir»/«Instalar» de esta versión)"
  fi
  # shellcheck disable=SC2086
  if ! out="$(dc up -d --wait --wait-timeout 300 $servicios 2>&1)"; then
    printf '%s\n' "$out" | grep -v '^ *$' | tail -15
    fallo "El nodo no arrancó completo. Botón «Registros» para ver el detalle."
  fi
  ok "Servicios arriba"
  paso "5/$total Comprobando el sitio por HTTPS"
  local i good=0
  for i in $(seq 1 20); do
    if https_local events.adventist.club /nodo-salud && https_local api.adventist.club /api/v1/health; then good=1; break; fi
    sleep 2
  done
  [ $good = 1 ] && ok "https://events.adventist.club responde en esta Mac" || aviso "HTTPS todavía no responde; espera un minuto y mira el estado"
  paso "6/$total Evitando que la Mac se duerma, respaldos, puerta y nodos vecinos"
  start_awake
  start_mirror
  start_puerta
  start_malla_bonjour
  date '+%Y-%m-%d %H:%M:%S' > "$RUN_DIR/encendido"
  paso "7/$total Red"
  detect_net
  if [ "$NET_EN_ROUTER" = 1 ]; then
    ok "En la red del GL.iNet: esta Mac es $NET_IP"
    [ -n "$IP_FIJA" ] && [ "$IP_FIJA" != "$NET_IP" ] && aviso "La IP cambió ($IP_FIJA → $NET_IP): vuelve a «Configurar router»"
  else
    aviso "Conecta el GL.iNet (cable o Wi-Fi). El nodo ya está encendido; en cuanto aparezca la red, todo sigue solo."
  fi
  # El alcance de este nodo para el panel (si ya se preparó un evento).
  [ -n "$EVENTO_ID" ] && { nodo_info_guardar "$EVENTO_ID" >/dev/null 2>&1 || true; }
  ok "Nodo encendido"
}

cmd_apagar() {
  need_node_dir
  paso "1/3 Último respaldo"
  if [ "$(service_state db)" = healthy ]; then cmd_respaldar || aviso "No se pudo hacer el último respaldo"; else say "la base ya estaba apagada"; fi
  paso "2/3 Deteniendo servicios"
  if docker_ok; then
    dc_nosecrets down --remove-orphans 2>&1 | grep -v '^ *$' | tail -8
  fi
  ok "Servicios detenidos (los datos se conservan)"
  paso "3/3 La Mac ya puede dormir"
  agent_unload despierto
  agent_unload respaldo-usb
  agent_unload puerta
  agent_unload malla
  rm -f "$RUN_DIR/encendido" "$RUN_DIR/puerta.json"
  ok "Nodo apagado"
}

puede_construir() {
  [ -n "$BACKEND_DIR" ] && [ -f "$BACKEND_DIR/requirements.txt" ] && [ -n "$FRONTEND_DIR" ] && [ -f "$FRONTEND_DIR/package.json" ]
}

cmd_construir() {
  need_node_dir
  puede_construir || fallo "«Construir» necesita los repos (backend y frontend) en esta Mac. La versión portátil ya trae el sistema: usa «Instalar»."
  [ -d "$BACKEND_DIR" ] || fallo "No encuentro el backend en $BACKEND_DIR"
  [ -d "$FRONTEND_DIR" ] || fallo "No encuentro el frontend en $FRONTEND_DIR (FRONTEND_DIR en la configuración)"
  paso "1/3 Encendiendo Docker"
  start_colima
  paso "2/3 Construyendo (necesita internet; 5–15 min la primera vez)"
  local bid; bid="$(git -C "$FRONTEND_DIR" rev-parse --short HEAD 2>/dev/null || echo nodo-local)"
  config_set WEB_BUILD_ID "$bid"; WEB_BUILD_ID="$bid"
  dc_nosecrets build migrate web 2>&1 | grep -vE '^ *$' | grep -E 'Built|ERROR|error|=>|#[0-9]+ (DONE|ERROR)' | tail -40
  [ "${PIPESTATUS[0]}" = 0 ] || fallo "La construcción falló (¿hay internet?)"
  paso "3/3 Descargando imágenes base (Postgres, Caddy, lego)"
  docker pull -q postgres:16-alpine >/dev/null && docker pull -q caddy:2.10-alpine >/dev/null \
    && docker pull -q goacme/lego:v4.26.0 >/dev/null || aviso "No se pudieron descargar todas las imágenes base"
  ok "Sistema construido (web: $bid)"
}

# ---------------------------------------------------------------------------------------------
# Export / import (lo único que sale a internet)
cmd_sync() {
  need_node_dir
  local sub="$1"; shift
  [ -n "${PROD_DATABASE_URL:-}" ] || fallo "Falta la conexión de producción (Ajustes › Secretos)."
  [ "$(service_state db)" = healthy ] || fallo "Primero enciende el nodo (la copia se guarda en su base)."
  need_secrets
  local args=(--event "$EVENTO_ID")
  if [ "$sub" != status ]; then
    [ -n "$ACTOR_EMAIL" ] || fallo "Falta el correo de coordinación que firma (Preparar evento › correo)."
    args+=(--actor-email "$ACTOR_EMAIL")
  fi
  [ "$sub" = export ] && args+=(--node-label "$NODO_LABEL")
  say "$( [ "$sub" = export ] && echo 'Copiando de producción al nodo' || { [ "$sub" = import ] && echo 'Subiendo del nodo a producción' || echo 'Consultando'; } ) · evento $EVENTO_ID"
  # -e PROD_DATABASE_URL sin valor: Docker lo toma del entorno; no queda en la línea de órdenes.
  # PROD_EVENT_PASS_SECRET (opcional): el secreto de pases de PRODUCCIÓN; con él el script vuelve a
  # cifrar los pases con el secreto propio del nodo (EVENT_PASS_SECRET, generado por la app).
  local extra=(-e PROD_DATABASE_URL)
  [ -n "${PROD_EVENT_PASS_SECRET:-}" ] && extra+=(-e PROD_EVENT_PASS_SECRET)
  dc --profile sync run --rm -T "${extra[@]}" sync "$sub" "${args[@]}" "$@" 2>&1 \
    | grep -vE '^ *(Container|Network) .*(Running|Creating|Created|Starting|Started|Waiting|Healthy)'
  local rc="${PIPESTATUS[0]}"
  case "$rc" in
    0) ok "Terminado" ;;
    2) printf '@@conflictos\n'; aviso "Hay conflictos: elige quién gana o déjalos pendientes" ;;
    3) printf '@@error La base del nodo y la de producción tienen versiones distintas (actualiza el nodo con «Construir»)\n' ;;
    *) printf '@@error Falló (código %s). Revisa el reporte de arriba.\n' "$rc" ;;
  esac
  return "$rc"
}

# ---------------------------------------------------------------------------------------------
# Modo automático: la app inició sesión en adventist.club y pasa el token SÓLO por el entorno
# (NODO_API_TOKEN). El contenedor `sync` habla con la API (DNS público, no el del router).
api_url_contenedor() {
  # La base de la API para el contenedor. Pruebas: NODO_API_BASE=http://127.0.0.1:8000/api/v1 →
  # host.docker.internal (el «localhost» de la Mac visto desde Docker).
  local u="${NODO_API_BASE:-https://api.adventist.club/api/v1}"
  u="${u/:\/\/localhost/://host.docker.internal}"
  u="${u/:\/\/127.0.0.1/://host.docker.internal}"
  printf '%s' "$u"
}

# Corre `nodo_sync.py <orden>` en el contenedor sync con el token. Las líneas @@paso del script se
# muestran como sub-pasos del paso actual del motor ($SYNC_PASO, p. ej. «4/6»).
sync_auto() {
  local orden="$1"; shift
  export NODO_API_URL; NODO_API_URL="$(api_url_contenedor)"
  local extra=(-e NODO_API_URL)
  [ -n "${NODO_API_TOKEN:-}" ] && extra+=(-e NODO_API_TOKEN)
  dc --profile sync run --rm -T "${extra[@]}" sync "$orden" "$@" 2>&1 \
    | grep --line-buffered -vE '^ *(Container|Network) .*(Running|Creating|Created|Starting|Started|Waiting|Healthy)' \
    | if [ -n "${SYNC_PASO:-}" ]; then sed -u -E "s#^@@paso [0-9]+/[0-9]+ #@@paso ${SYNC_PASO} #"; else cat; fi
  return "${PIPESTATUS[0]}"
}

sync_rc_mensaje() {
  case "$1" in
    0) ok "Terminado" ;;
    2) printf '@@conflictos\n'; aviso "Hay conflictos: elige quién gana o déjalos pendientes" ;;
    3) printf '@@error La base del nodo y la de producción tienen versiones distintas (actualiza el nodo)\n' ;;
    4) printf '@@relogin\n'; printf '@@error La sesión de adventist.club venció: vuelve a iniciar sesión\n' ;;
    5) printf '@@error Sin permiso para este evento, o tu cuenta pide el código de verificación (MFA)\n' ;;
    *) printf '@@error Falló (código %s). Revisa el detalle de arriba.\n' "$1" ;;
  esac
}

base_del_nodo() {  # Docker + base + migraciones (sin levantar el sitio: «Preparar» puede ir antes de «Encender»)
  start_colima
  docker image inspect "nodo-evento-api:${NODO_TAG:-local}" >/dev/null 2>&1 || {
    if [ "$PAQUETE" = 1 ]; then cargar_imagenes; else fallo "Falta construir el sistema (Ajustes › «Construir», con internet, una sola vez)."; fi; }
  local out
  if [ "$(service_state db)" != healthy ]; then
    say "Arrancando la base del nodo…"
    out="$(dc up -d --wait --wait-timeout 180 db 2>&1)" || { printf '%s\n' "$out" | tail -8; fallo "La base del nodo no arrancó"; }
  fi
  # Migraciones siempre (idempotentes): tras actualizar la app, la base del nodo queda al día.
  out="$(dc run --rm -T migrate 2>&1)" || { printf '%s\n' "$out" | tail -12; fallo "No se pudo preparar la base del nodo (migraciones)"; }
  ok "Base del nodo lista"
}

cert_huella() { [ -s "$NODO_DIR/tls/actual/fullchain.pem" ] && shasum -a 256 "$NODO_DIR/tls/actual/fullchain.pem" | cut -c1-64; }

cmd_preparar_auto() {
  need_node_dir
  local motivo; motivo="$(instalacion_pendiente)" && fallo "Falta instalar ($motivo): pulsa «Instalar»."
  local ev="" label="" acts="" todo=0 llegadas="" reemp_copia=0 reemp_nodo=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --event) ev="$2"; shift ;;
      --label) label="$2"; shift ;;
      --actividades) acts="$2"; shift ;;
      --todo) todo=1 ;;
      --llegadas) llegadas=1 ;;
      --sin-llegadas) llegadas=0 ;;
      --reemplazar-copia) reemp_copia=1 ;;
      --reemplazar-nodo) reemp_nodo="$2"; shift ;;
      *) fallo "opción desconocida: $1" ;;
    esac
    shift
  done
  [ -n "$ev" ] || fallo "Falta el evento (--event)."
  [ -n "$label" ] || fallo "Falta el nombre del nodo (--label)."
  [ "$todo" = 1 ] || [ -n "$acts" ] || [ "$llegadas" = 1 ] || fallo "Elige qué captura este nodo: actividades, «Todas» o la mesa de llegadas."
  [ -n "${NODO_API_TOKEN:-}" ] || { printf '@@relogin\n'; fallo "Primero «Conectar con adventist.club»."; }
  need_secrets
  local total=6
  paso "1/$total Revisando la Mac"
  ok "Evento $ev · nodo «${label}»"
  paso "2/$total Encendiendo Docker y la base del nodo"
  base_del_nodo
  mkdir -p "$NODO_DIR/tls/actual" "$NODO_DIR/datos/exports" "$NODO_DIR/datos/media"
  local antes; antes="$(cert_huella)"
  paso "3/$total Preparando desde adventist.club (1–3 min)"
  local args=(--event "$ev" --node-label "$label" --tls-dir /tls/actual)
  if [ "$todo" = 1 ]; then args+=(--all); elif [ -n "$acts" ]; then args+=(--activities "$acts"); fi
  case "$llegadas" in 1) args+=(--check-in) ;; 0) args+=(--no-check-in) ;; esac
  [ "$reemp_copia" = 1 ] && args+=(--replace-node-copy)
  [ -n "$reemp_nodo" ] && args+=(--replace-node "$reemp_nodo")
  SYNC_PASO="3/$total" sync_auto prepare "${args[@]}"
  local rc=$?
  if [ "$rc" != 0 ]; then sync_rc_mensaje "$rc"; return "$rc"; fi
  config_set EVENTO_ID "$ev"; EVENTO_ID="$ev"
  config_set NODO_LABEL "$label"; NODO_LABEL="$label"
  paso "4/$total Certificado HTTPS"
  local despues; despues="$(cert_huella)"
  if [ -n "$despues" ] && [ "$despues" != "$antes" ]; then
    # Llegó un certificado con el paquete. Si no es de la CA local, se quita la CA local vieja
    # (si no, el panel lo seguiría marcando como «plan B» y Caddy repartiría la CA).
    if ! openssl x509 -noout -issuer -in "$NODO_DIR/tls/actual/fullchain.pem" 2>/dev/null | grep -q 'Nodo evento CA local'; then
      rm -f "$NODO_DIR/tls/actual/ca-local.pem"
    fi
    chmod 600 "$NODO_DIR/tls/actual/privkey.pem" 2>/dev/null
    cert_info; ok "Certificado instalado (vence $CERT_VENCE)"
    caddy_reiniciar
  elif [ -n "$despues" ]; then
    cert_info; ok "Se conserva el certificado que ya tenía esta Mac (vence $CERT_VENCE)"
  else
    aviso "Producción no mandó certificado: creo uno local (plan B; cada teléfono debe instalar el perfil)"
    cert_local_crear >/dev/null || aviso "No se pudo crear el certificado local"
    caddy_reiniciar
  fi
  paso "5/$total Alcance de este nodo"
  nodo_info_guardar "$ev" >/dev/null 2>&1 || aviso "No pude leer el alcance del nodo (se verá al encender)"
  paso "6/$total Listo"
  ok "Evento preparado en esta Mac. Siguiente: «Encender»."
  return 0
}

cmd_subir_auto() {
  need_node_dir
  local ev="$EVENTO_ID" args=() seco=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --event) ev="$2"; shift ;;
      --dry-run) args+=(--dry-run); seco=1 ;;
      --prefer) args+=(--prefer "$2"); shift ;;
      --final) args+=(--final) ;;
      --avance|--partial) args+=(--partial) ;;
      *) fallo "opción desconocida: $1" ;;
    esac
    shift
  done
  [ -n "$ev" ] || fallo "Falta el evento (--event)."
  [ -n "${NODO_API_TOKEN:-}" ] || { printf '@@relogin\n'; fallo "Primero «Conectar con adventist.club»."; }
  need_secrets
  [ "$(service_state db)" = healthy ] || fallo "Primero enciende el nodo (los resultados están en su base)."
  local total=3
  if [ "$seco" = 1 ]; then
    paso "1/$total Revisando qué se subiría (no cambia nada)"
  else
    paso "1/$total Respaldo antes de subir"
    cmd_respaldar || fallo "No se pudo respaldar: no subo nada sin respaldo"
  fi
  paso "2/$total Hablando con adventist.club"
  SYNC_PASO="2/$total" sync_auto upload --event "$ev" ${args[@]+"${args[@]}"}
  local rc=$?
  paso "3/$total Terminando"
  [ "$seco" = 1 ] || nodo_info_guardar "$ev" >/dev/null 2>&1 || true
  sync_rc_mensaje "$rc"
  return "$rc"
}

# Alcance de ESTE nodo (fila local de event_offline_nodes), sin red. Se guarda en run/nodo-info.json
# para que «estado» lo muestre sin correr un contenedor cada 5 s.
nodo_info_guardar() {
  local ev="${1:-$EVENTO_ID}" out js
  [ -n "$ev" ] || return 1
  out="$(sync_auto node-info --event "$ev")"; local rc=$?
  printf '%s\n' "$out" | grep -v '^@@json '
  js="$(printf '%s\n' "$out" | sed -n 's/^@@json //p' | tail -1)"
  if [ "$rc" = 0 ] && [ -n "$js" ]; then
    printf '%s\n' "$js" > "$RUN_DIR/nodo-info.json.tmp" && mv "$RUN_DIR/nodo-info.json.tmp" "$RUN_DIR/nodo-info.json"
    printf '@@json %s\n' "$js"
  fi
  return "$rc"
}

cmd_nodo_info() {
  need_node_dir
  local ev="$EVENTO_ID"
  [ "${1:-}" = --event ] && ev="${2:-}"
  [ "$(service_state db)" = healthy ] || fallo "El nodo está apagado (la información está en su base)."
  nodo_info_guardar "$ev"
}

# ---------------------------------------------------------------------------------------------
# Router GL.iNet (OpenWrt) por SSH + uci
sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

router_usuario() {
  if [ -n "$ROUTER_USUARIO" ]; then printf '%s' "$ROUTER_USUARIO"
  elif [ "$ROUTER_TIPO" = mikrotik ]; then printf admin
  else printf root; fi
}

router_ssh() {  # router_ssh < script   (GL.iNet/OpenWrt: el guion va por stdin a «sh -s»;
                #  MikroTik: RouterOS no tiene shell, el guion va como orden remota)
  [ -n "${NODO_ROUTER_PASS:-}" ] || fallo "Falta la contraseña de administración del router."
  local askpass="$RUN_DIR/askpass.sh" remoto='sh -s' entrada
  # El ayudante sólo lee la contraseña del entorno (nunca la escribe en disco).
  printf '#!/bin/sh\nprintf "%%s\\n" "$NODO_ROUTER_PASS"\n' > "$askpass"; chmod 700 "$askpass"
  entrada="$(cat)"
  if [ "$ROUTER_TIPO" = mikrotik ]; then remoto="$entrada"; entrada=""; fi
  printf '%s\n' "$entrada" | SSH_ASKPASS="$askpass" SSH_ASKPASS_REQUIRE=force DISPLAY="${DISPLAY:-:0}" \
  ssh -T -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$APP_SUPPORT/known_hosts_router" \
      -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive \
      -o NumberOfPasswordPrompts=1 -o ConnectTimeout=8 -o HostKeyAlgorithms=+ssh-rsa \
      "$(router_usuario)@$ROUTER_IP" "$remoto"
}

# MikroTik (RouterOS 6/7): reserva DHCP + DNS estático, marcados con el comentario «nodo-camporee»
# para que repetirlo reemplace lo anterior. Una orden por línea, sin variables (RouterOS las olvida
# entre líneas en una sesión SSH). No toca Wi-Fi, firewall ni el resto de la configuración.
router_apply_script_mikrotik() {
  local ip="$1" mac d; mac="$(printf '%s' "$2" | tr '[:lower:]' '[:upper:]')"
  printf '%s\n' \
    '/ip dhcp-server lease remove [find comment="nodo-camporee"]' \
    "/ip dhcp-server lease remove [find mac-address=\"$mac\"]" \
    "/ip dhcp-server lease remove [find address=\"$ip\"]" \
    "/ip dhcp-server lease add address=$ip mac-address=$mac comment=\"nodo-camporee\"" \
    '/ip dns static remove [find comment="nodo-camporee"]'
  for d in $DOMINIOS; do
    printf '/ip dns static remove [find name="%s"]\n' "$d"
    printf '/ip dns static add name="%s" address=%s ttl=1m comment="nodo-camporee"\n' "$d" "$ip"
  done
  printf '%s\n' '/ip dns set allow-remote-requests=yes' ':put "router: reserva y DNS aplicados"'
}

router_read_script_mikrotik() {
  printf '%s\n' \
    ':put ("modelo: " . [/system resource get board-name] . " · RouterOS " . [/system resource get version])' \
    '/ip dhcp-server lease print where comment="nodo-camporee"' \
    '/ip dns static print where comment="nodo-camporee"' \
    '/ip dhcp-server network print' \
    ':put ("DNS para la red: " . [/ip dns get allow-remote-requests])'
}

router_guion_leer() { if [ "$ROUTER_TIPO" = mikrotik ]; then router_read_script_mikrotik; else router_read_script; fi; }
router_guion_aplicar() { if [ "$ROUTER_TIPO" = mikrotik ]; then router_apply_script_mikrotik "$@"; else router_apply_script "$@"; fi; }

# ---------------------------------------------------------------------------------------------
# «Red pública del evento»: además de la reserva y los nombres del evento (lo de siempre):
#   • DNS con lista permitida: sólo events/api/admin/media.adventist.club y nodo.local → el nodo;
#     todo lo demás → 0.0.0.0 (sumidero). Opcional: las pruebas de «¿hay internet?» → el nodo.
#   • Sin desvíos: los equipos (salvo la Mac) no pueden usar otro DNS (53/853) ni DoH conocidos.
#   • Wi-Fi con aislamiento de clientes (la Mac va por CABLE y sigue al alcance de todos).
#   • La Mac recibe DNS públicos por DHCP (para «Subir avance» si el WAN tiene internet) y,
#     opcional, es la ÚNICA que sale a internet.
# Todo lo que se agrega lleva el nombre/comentario «nodo_*» / «nodo-camporee-publica» y
# «red-publica-quitar» lo deshace (deja la configuración normal del router).
PORTAL_HOSTS="captive.apple.com www.apple.com www.appleiphonecell.com www.itools.info www.ibook.info www.airport.us www.thinkdifferent.us connectivitycheck.gstatic.com connectivitycheck.android.com clients3.google.com clients1.google.com www.google.com www.gstatic.com www.msftconnecttest.com www.msftncsi.com detectportal.firefox.com nmcheck.gnome.org"
DOH_IPS="1.1.1.1 1.0.0.1 8.8.8.8 8.8.4.4 9.9.9.9 149.112.112.112 94.140.14.14 94.140.15.15 208.67.222.222 208.67.220.220 45.90.28.0 45.90.30.0 185.228.168.9 76.76.2.0 76.76.10.0"

publica_script_openwrt() {  # publica_script_openwrt IP aplicar|quitar
  local ip="$1" modo="$2" h
  cat <<EOF
set -e
IP=$(sq "$ip")
# Lo anterior de la red pública (idempotente)
for v in \$(uci -q get dhcp.@dnsmasq[0].address || true); do
  case "\$v" in /\#/*|/nodo.local/*) uci del_list dhcp.@dnsmasq[0].address="\$v" ;; esac
done
EOF
  for h in $PORTAL_HOSTS; do
    printf 'for v in $(uci -q get dhcp.@dnsmasq[0].address || true); do case "$v" in /%s/*) uci del_list dhcp.@dnsmasq[0].address="$v" ;; esac; done\n' "$h"
  done
  cat <<'EOF'
uci -q delete dhcp.nodo_camporee.tag || true
uci -q delete dhcp.nodocamporee || true
for r in nodo_dns nodo_doh nodo_solo_mac; do uci -q delete firewall.$r || true; done
EOF
  if [ "$modo" = quitar ]; then
    cat <<'EOF'
for s in $(uci -X show wireless | sed -n "s/^wireless\.\([^.=]*\)=wifi-iface$/\1/p"); do
  [ "$(uci -q get wireless.$s.network)" = lan ] && uci -q delete wireless.$s.isolate || true
done
uci commit dhcp; uci commit firewall; uci commit wireless
/etc/init.d/dnsmasq restart >/dev/null 2>&1 || /etc/init.d/dnsmasq reload
/etc/init.d/firewall reload >/dev/null 2>&1 || true
( sleep 3; wifi reload ) >/dev/null 2>&1 &
echo "router: red pública quitada (queda la configuración normal del nodo)"
EOF
    return 0
  fi
  cat <<'EOF'
# DNS: lista permitida (lo más específico gana en dnsmasq) + sumidero para todo lo demás
uci add_list dhcp.@dnsmasq[0].address="/nodo.local/$IP"
uci add_list dhcp.@dnsmasq[0].address='/#/0.0.0.0'
EOF
  if [ "${RED_PORTAL:-exito}" != ninguno ]; then
    for h in $PORTAL_HOSTS; do printf 'uci add_list dhcp.@dnsmasq[0].address="/%s/$IP"\n' "$h"; done
  fi
  cat <<EOF
# La Mac del nodo recibe DNS públicos (puede subir resultados si el WAN tiene internet)
uci set dhcp.nodocamporee=tag
uci add_list dhcp.nodocamporee.dhcp_option='6,1.1.1.1,8.8.8.8'
uci set dhcp.nodo_camporee.tag='nodocamporee'
# Sin desvíos de DNS (salvo la Mac)
uci set firewall.nodo_dns=rule
uci set firewall.nodo_dns.name='nodo: sólo el DNS del router'
uci set firewall.nodo_dns.src='lan'; uci set firewall.nodo_dns.dest='wan'
uci set firewall.nodo_dns.proto='tcp udp'; uci set firewall.nodo_dns.dest_port='53 853'
uci set firewall.nodo_dns.src_ip="!\$IP"; uci set firewall.nodo_dns.target='REJECT'
uci set firewall.nodo_doh=rule
uci set firewall.nodo_doh.name='nodo: sin DNS cifrado (DoH) conocido'
uci set firewall.nodo_doh.src='lan'; uci set firewall.nodo_doh.dest='wan'
uci set firewall.nodo_doh.proto='tcp udp'; uci set firewall.nodo_doh.dest_port='443'
uci set firewall.nodo_doh.src_ip="!\$IP"; uci set firewall.nodo_doh.target='REJECT'
EOF
  for h in $DOH_IPS; do printf "uci add_list firewall.nodo_doh.dest_ip='%s'\n" "$h"; done
  if [ "${PUBLICA_SOLO_MAC:-1}" = 1 ]; then
    cat <<'EOF'
uci set firewall.nodo_solo_mac=rule
uci set firewall.nodo_solo_mac.name='nodo: sólo la Mac del nodo sale a internet'
uci set firewall.nodo_solo_mac.src='lan'; uci set firewall.nodo_solo_mac.dest='wan'
uci set firewall.nodo_solo_mac.proto='all'; uci set firewall.nodo_solo_mac.src_ip="!$IP"
uci set firewall.nodo_solo_mac.target='REJECT'
EOF
  fi
  cat <<EOF
# Wi-Fi: aislamiento de clientes$( [ -n "$PUBLICA_SSID" ] && printf ' + nombre «%s»' "$PUBLICA_SSID")
SSID=$(sq "$PUBLICA_SSID"); ABIERTA=$(sq "${PUBLICA_ABIERTA:-1}"); KEY=$(sq "${WIFI_PASSWORD:-}")
n=0
for s in \$(uci -X show wireless | sed -n "s/^wireless\\.\\([^.=]*\\)=wifi-iface\$/\\1/p"); do
  [ "\$(uci -q get wireless.\$s.mode)" = ap ] || continue
  [ "\$(uci -q get wireless.\$s.network)" = lan ] || continue
  uci set wireless.\$s.isolate='1'
  [ -n "\$SSID" ] && uci set wireless.\$s.ssid="\$SSID"
  if [ "\$ABIERTA" = 1 ]; then uci set wireless.\$s.encryption='none'; uci -q delete wireless.\$s.key || true
  elif [ -n "\$KEY" ]; then uci set wireless.\$s.encryption='psk2'; uci set wireless.\$s.key="\$KEY"; fi
  n=\$((n+1))
done
uci commit dhcp; uci commit firewall; uci commit wireless
/etc/init.d/dnsmasq restart >/dev/null 2>&1 || /etc/init.d/dnsmasq reload
/etc/init.d/firewall reload >/dev/null 2>&1 || true
echo "router: red pública aplicada (\$n radios; el Wi-Fi se reinicia en 3 s)"
( sleep 3; wifi reload ) >/dev/null 2>&1 &
EOF
}

publica_script_mikrotik() {  # publica_script_mikrotik IP aplicar|quitar
  local ip="$1" modo="$2" h
  printf '%s\n' \
    '/ip dns static remove [find comment="nodo-camporee-publica"]' \
    '/ip firewall filter remove [find comment="nodo-camporee-publica"]' \
    '/ip firewall address-list remove [find list="nodo-doh"]' \
    '/ip dhcp-server lease set [find comment="nodo-camporee"] dhcp-option=""' \
    '/ip dhcp-server option remove [find name="nodo-dns"]'
  if [ "$modo" = quitar ]; then printf '%s\n' ':put "router: red pública quitada"'; return 0; fi
  printf '/ip dns static add name="nodo.local" address=%s ttl=1m comment="nodo-camporee-publica"\n' "$ip"
  if [ "${RED_PORTAL:-exito}" != ninguno ]; then
    for h in $PORTAL_HOSTS; do printf '/ip dns static add name="%s" address=%s ttl=1m comment="nodo-camporee-publica"\n' "$h" "$ip"; done
  fi
  # El sumidero va AL FINAL: RouterOS revisa las entradas estáticas en orden.
  printf '%s\n' '/ip dns static add regexp="." address=0.0.0.0 ttl=1m comment="nodo-camporee-publica"' \
    "/ip dhcp-server option add name=\"nodo-dns\" code=6 value=\"'1.1.1.1'\"" \
    '/ip dhcp-server lease set [find comment="nodo-camporee"] dhcp-option="nodo-dns"'
  for h in $DOH_IPS; do printf '/ip firewall address-list add list="nodo-doh" address=%s\n' "$h"; done
  printf '/ip firewall filter add chain=forward src-address=!%s protocol=udp dst-port=53,853 action=reject comment="nodo-camporee-publica"\n' "$ip"
  printf '/ip firewall filter add chain=forward src-address=!%s protocol=tcp dst-port=53,853 action=reject comment="nodo-camporee-publica"\n' "$ip"
  printf '/ip firewall filter add chain=forward src-address=!%s dst-address-list=nodo-doh protocol=tcp dst-port=443 action=reject comment="nodo-camporee-publica"\n' "$ip"
  if [ "${PUBLICA_SOLO_MAC:-1}" = 1 ]; then
    printf '/ip firewall filter add chain=forward src-address=!%s out-interface-list=WAN action=reject comment="nodo-camporee-publica"\n' "$ip"
  fi
  printf '%s\n' ':put "router: red pública aplicada (Wi-Fi y aislamiento: a mano en Wireless/WiFi › default-forwarding=no / client-isolation)"'
}

cmd_red_publica() {  # cmd_red_publica plan|aplicar|quitar [--simular] [--tipo …]
  local modo="$1"; shift
  local simular=0
  WIFI_SSID=""   # el nombre del Wi-Fi de la red pública es PUBLICA_SSID
  while [ $# -gt 0 ]; do
    case "$1" in --simular) simular=1 ;; --tipo) ROUTER_TIPO="$2"; shift ;; esac
    shift
  done
  case "$ROUTER_TIPO" in
    glinet) ;;
    openwrt|mikrotik) ROUTER_IP="${ROUTER_IP_OTRO:-${GATEWAY:-$ROUTER_IP}}" ;;
    *) fallo "La red pública se configura sola en GL.iNet, OpenWrt o MikroTik (ver la lista para otros routers)." ;;
  esac
  router_target
  [ "$NET_KIND" = ethernet ] || aviso "La Mac debe ir por CABLE al router: con el aislamiento de clientes, por Wi-Fi los teléfonos no la verían."
  say "Router: $ROUTER_IP ($ROUTER_TIPO) · esta Mac: $ROUTER_T_IP (MAC $ROUTER_T_MAC)"
  say ""
  if [ "$modo" = quitar ]; then
    say "Se quita la red pública (queda lo normal: reserva y nombres del evento → esta Mac)."
  else
    say "Se va a dejar así en el router:"
    say "  • Reserva DHCP: MAC $ROUTER_T_MAC → IP $ROUTER_T_IP, con DNS públicos sólo para esta Mac"
    say "  • DNS: $DOMINIOS nodo.local → $ROUTER_T_IP; TODO lo demás → 0.0.0.0"
    case "${RED_PORTAL:-exito}" in
      exito) say "  • Pruebas de «¿hay internet?» de los teléfonos → esta Mac contesta «sí» (se quedan en el Wi-Fi)" ;;
      bienvenida) say "  • Pruebas de «¿hay internet?» → página de bienvenida «Entrar a events.adventist.club»" ;;
      *) say "  • Pruebas de «¿hay internet?»: sin respuesta (el teléfono dirá «sin internet»)" ;;
    esac
    say "  • Nadie (salvo esta Mac) puede usar otro DNS (puertos 53/853) ni DNS cifrado conocido"
    [ "${PUBLICA_SOLO_MAC:-1}" = 1 ] && say "  • Sólo esta Mac sale a internet (si el WAN tiene)"
    if [ "$ROUTER_TIPO" = mikrotik ]; then say "  • Wi-Fi y aislamiento: a mano (RouterOS cambia según el paquete de Wi-Fi)"
    else say "  • Wi-Fi: aislamiento de clientes$( [ -n "$PUBLICA_SSID" ] && printf ', nombre «%s»' "$PUBLICA_SSID")$( [ "${PUBLICA_ABIERTA:-1}" = 1 ] && printf ', SIN contraseña' || printf ', con la contraseña guardada')"; fi
  fi
  local guion
  if [ "$modo" = plan ] || [ "$simular" = 1 ]; then
    if [ "$modo" = quitar ]; then
      guion="$(if [ "$ROUTER_TIPO" = mikrotik ]; then publica_script_mikrotik "$ROUTER_T_IP" quitar; else publica_script_openwrt "$ROUTER_T_IP" quitar; fi)"
    else
      guion="$(router_guion_aplicar "$ROUTER_T_IP" "$ROUTER_T_MAC"; if [ "$ROUTER_TIPO" = mikrotik ]; then publica_script_mikrotik "$ROUTER_T_IP" aplicar; else publica_script_openwrt "$ROUTER_T_IP" aplicar; fi)"
    fi
    say ""; say "SIMULACIÓN (no se conectó al router). Órdenes que se ejecutarían por SSH:"
    say "-----"; printf '%s\n' "$guion" | sed 's/KEY=.*/KEY=<oculta>/'; say "-----"
    ok "Simulación terminada"
    return 0
  fi
  router_alive || fallo "No veo el router en $ROUTER_IP. ¿Esta Mac está conectada por cable?"
  if [ "$modo" = quitar ]; then
    { if [ "$ROUTER_TIPO" = mikrotik ]; then publica_script_mikrotik "$ROUTER_T_IP" quitar; else publica_script_openwrt "$ROUTER_T_IP" quitar; fi; } | router_ssh 2>&1 | depurar | sed 's/^/  /'
    [ "${PIPESTATUS[1]}" = 0 ] || fallo "El router no aceptó los cambios."
    ok "Red pública quitada"
    return 0
  fi
  { router_guion_aplicar "$ROUTER_T_IP" "$ROUTER_T_MAC"
    if [ "$ROUTER_TIPO" = mikrotik ]; then publica_script_mikrotik "$ROUTER_T_IP" aplicar; else publica_script_openwrt "$ROUTER_T_IP" aplicar; fi
  } | router_ssh 2>&1 | depurar | sed 's/^/  /'
  [ "${PIPESTATUS[1]}" = 0 ] || fallo "El router no aceptó los cambios (contraseña o firmware)."
  config_set IP_FIJA "$ROUTER_T_IP"; config_set MAC_FIJA "$ROUTER_T_MAC"
  # Caddy contesta las pruebas de «¿hay internet?» según RED_PORTAL (se aplica al reiniciarlo).
  if docker inspect "$(container caddy)" >/dev/null 2>&1; then dc up -d --no-deps caddy >/dev/null 2>&1 && say "Caddy actualizado (portal: ${RED_PORTAL:-exito})"; fi
  ok "Red pública aplicada: esta Mac = $ROUTER_T_IP. Pulsa «Revisar»."
}

router_target() {  # decide IP y MAC a reservar: ROUTER_T_IP ROUTER_T_MAC ROUTER_T_IFACE
  detect_net
  ROUTER_T_IFACE="$NET_IFACE"; ROUTER_T_MAC="$NET_MAC"
  if [ "$NET_EN_ROUTER" = 1 ]; then ROUTER_T_IP="$NET_IP"
  elif [ -n "$IP_FIJA" ]; then ROUTER_T_IP="$IP_FIJA"
  else ROUTER_T_IP="$(router_prefix)10"; fi
  [ -n "${NODO_ROUTER_IP_OVERRIDE:-}" ] && ROUTER_T_IP="$NODO_ROUTER_IP_OVERRIDE"
  [ -n "${NODO_ROUTER_MAC_OVERRIDE:-}" ] && ROUTER_T_MAC="$NODO_ROUTER_MAC_OVERRIDE"
  [ -n "$ROUTER_T_MAC" ] || fallo "No encuentro la dirección MAC de esta Mac."
}

router_apply_script() {
  local ip="$1" mac="$2" d
  cat <<EOF
set -e
IP=$(sq "$ip"); MAC=$(sq "$mac")
# 1) Reserva DHCP: esta Mac siempre recibe \$IP (sección con nombre fijo => idempotente).
for s in \$(uci -X show dhcp | sed -n "s/^dhcp\\.\\([^.=]*\\)=host\$/\\1/p"); do
  [ "\$s" = nodo_camporee ] && continue
  m="\$(uci -q get dhcp.\$s.mac || true)"; i="\$(uci -q get dhcp.\$s.ip || true)"
  if [ "\$i" = "\$IP" ] || echo "\$m" | grep -qi "\$MAC"; then echo "quito reserva anterior \$s (\$m \$i)"; uci delete dhcp.\$s; fi
done
uci -q delete dhcp.nodo_camporee || true
uci set dhcp.nodo_camporee=host
uci set dhcp.nodo_camporee.name='nodo-camporee'
uci set dhcp.nodo_camporee.mac="\$MAC"
uci set dhcp.nodo_camporee.ip="\$IP"
uci set dhcp.nodo_camporee.leasetime='infinite'
# 2) DNS: los nombres del evento responden con la IP de esta Mac (también sin internet).
EOF
  for d in $DOMINIOS; do
    cat <<EOF
for v in \$(uci -q get dhcp.@dnsmasq[0].address || true); do case "\$v" in /$d/*) uci del_list dhcp.@dnsmasq[0].address="\$v" ;; esac; done
uci add_list dhcp.@dnsmasq[0].address="/$d/\$IP"
EOF
  done
  cat <<'EOF'
uci commit dhcp
/etc/init.d/dnsmasq restart >/dev/null 2>&1 || /etc/init.d/dnsmasq reload
echo "router: reserva y DNS aplicados"
EOF
  if [ -n "$WIFI_SSID" ]; then
    cat <<EOF
SSID=$(sq "$WIFI_SSID"); KEY=$(sq "${WIFI_PASSWORD:-}")
n=0
for s in \$(uci -X show wireless | sed -n "s/^wireless\\.\\([^.=]*\\)=wifi-iface\$/\\1/p"); do
  [ "\$(uci -q get wireless.\$s.mode)" = ap ] || continue
  [ "\$(uci -q get wireless.\$s.network)" = lan ] || continue
  uci set wireless.\$s.ssid="\$SSID"
  if [ -n "\$KEY" ]; then uci set wireless.\$s.encryption='psk2'; uci set wireless.\$s.key="\$KEY"; fi
  n=\$((n+1))
done
uci commit wireless
echo "router: Wi-Fi «\${SSID}» en \$n radios (se reinicia en 3 s)"
( sleep 3; wifi reload ) >/dev/null 2>&1 &
EOF
  fi
}

router_read_script() {
  cat <<'EOF'
echo "modelo: $(cat /tmp/sysinfo/model 2>/dev/null || uci -q get system.@system[0].hostname)"
echo "firmware: $(cat /etc/glversion 2>/dev/null || grep DISTRIB_RELEASE /etc/openwrt_release 2>/dev/null | cut -d= -f2)"
echo "reserva actual: $(uci -q get dhcp.nodo_camporee.mac || echo ninguna) $(uci -q get dhcp.nodo_camporee.ip || true)"
for v in $(uci -q get dhcp.@dnsmasq[0].address || true); do echo "dns actual: $v"; done
for s in $(uci -X show wireless | sed -n "s/^wireless\.\([^.=]*\)=wifi-iface$/\1/p"); do
  [ "$(uci -q get wireless.$s.mode)" = ap ] && [ "$(uci -q get wireless.$s.network)" = lan ] && echo "wifi actual: $s «$(uci -q get wireless.$s.ssid)»"
done
[ -x /etc/init.d/adguardhome ] && /etc/init.d/adguardhome enabled 2>/dev/null && echo "AVISO: AdGuard Home está activo; desactívalo (el DNS del evento pasa por dnsmasq)"
true
EOF
}

cmd_router() {
  local mode="$1"; shift
  local simular=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --simular) simular=1 ;;
      --tipo) ROUTER_TIPO="$2"; shift ;;
    esac
    shift
  done
  case "$ROUTER_TIPO" in
    glinet) ;;
    openwrt|mikrotik) ROUTER_IP="${ROUTER_IP_OTRO:-${GATEWAY:-$ROUTER_IP}}"; WIFI_SSID="" ;;   # el Wi-Fi de esos routers no se toca
    dns) fallo "Este router se configura a mano (DNS personalizado = esta Mac); no por SSH." ;;
    *) fallo "tipo de router desconocido: $ROUTER_TIPO" ;;
  esac
  router_target
  say "Router: $ROUTER_IP ($ROUTER_TIPO, usuario $(router_usuario)) · esta Mac: $ROUTER_T_IFACE ($( [ "$(iface_kind "$ROUTER_T_IFACE")" = ethernet ] && echo Ethernet || echo Wi-Fi )) MAC $ROUTER_T_MAC"
  [ "$(iface_kind "$ROUTER_T_IFACE")" = wifi ] && aviso "Por Wi-Fi: en Ajustes › Wi-Fi › (red del GL.iNet) › Detalles, pon «Dirección privada» en FIJA o desactivada; si no, la MAC puede cambiar. Mejor: adaptador Ethernet USB-C."
  say ""
  say "Se va a dejar así en el router:"
  say "  • Reserva DHCP: MAC $ROUTER_T_MAC  →  IP $ROUTER_T_IP (siempre la misma)"
  local d; for d in $DOMINIOS; do say "  • DNS: $d  →  $ROUTER_T_IP"; done
  [ -n "$WIFI_SSID" ] && say "  • Wi-Fi: nombre «${WIFI_SSID}»$( [ -n "${WIFI_PASSWORD:-}" ] && echo ' con la contraseña guardada' || echo ' (contraseña sin cambio)')"
  say "  • Nada más se toca (internet, contraseñas del panel, otras reservas)."
  if [ "$simular" = 1 ]; then
    say ""; say "SIMULACIÓN (no se conectó al router). Órdenes que se ejecutarían por SSH:"
    say "-----"
    if [ "$mode" = plan ]; then router_guion_leer; else router_guion_aplicar "$ROUTER_T_IP" "$ROUTER_T_MAC" | sed 's/KEY=.*/KEY=<oculta>/'; fi
    say "-----"
    ok "Simulación terminada"
    return 0
  fi
  router_alive || fallo "No veo el router en $ROUTER_IP. ¿Esta Mac está conectada a esa red?"
  if [ "$mode" = plan ]; then
    say ""; say "Valores actuales en el router:"
    router_guion_leer | router_ssh 2>&1 | depurar | sed 's/^/  /'
    [ "${PIPESTATUS[1]}" = 0 ] || fallo "No pude entrar por SSH (¿usuario «$(router_usuario)» y contraseña de administración correctos? ¿SSH activado en el router?)"
    ok "Revisa los cambios y pulsa «Aplicar»"
  else
    router_guion_aplicar "$ROUTER_T_IP" "$ROUTER_T_MAC" | router_ssh 2>&1 | depurar | sed 's/^/  /'
    [ "${PIPESTATUS[1]}" = 0 ] || fallo "El router no aceptó los cambios (contraseña o firmware)."
    config_set IP_FIJA "$ROUTER_T_IP"; config_set MAC_FIJA "$ROUTER_T_MAC"
    ok "Router configurado: esta Mac = $ROUTER_T_IP"
    [ "$NET_IP" != "$ROUTER_T_IP" ] && aviso "Desconecta y vuelve a conectar la red de la Mac para recibir $ROUTER_T_IP"
    sleep 2; cmd_router_revisar || true
  fi
}

cmd_router_revisar() {
  local d got bad=0 target="${IP_FIJA}"
  detect_net
  [ -n "$target" ] || target="$NET_IP"
  for d in $DOMINIOS; do
    got="$(dig +short +time=2 +tries=1 "@$ROUTER_IP" "$d" A 2>/dev/null | tail -1)"
    if [ "$got" = "$target" ]; then ok "DNS del router: $d → $got (esta Mac)"; else printf '@@aviso DNS del router: %s → %s (debería ser %s)\n' "$d" "${got:-sin respuesta}" "$target"; bad=1; fi
  done
  [ "$NET_IP" = "$target" ] || { aviso "Esta Mac tiene ahora $NET_IP, no $target"; bad=1; }
  [ $bad = 0 ]
}

# ---------------------------------------------------------------------------------------------
cert_local_crear() {
  detect_net
  local names=(events.adventist.club admin.adventist.club api.adventist.club media.adventist.club localhost)
  [ -n "$IP_FIJA" ] && names+=("$IP_FIJA")
  [ -n "$NET_IP" ] && [ "$NET_IP" != "$IP_FIJA" ] && names+=("$NET_IP")
  bash "$NODO_DIR/tls/ca-local.sh" "${names[@]}" 2>&1
}

caddy_reiniciar() {
  docker inspect "$(container caddy)" >/dev/null 2>&1 && docker restart "$(container caddy)" >/dev/null && say "Caddy reiniciado con el certificado nuevo"
  return 0
}

cmd_cert() {
  need_node_dir
  case "$1" in
    letsencrypt)
      [ -n "${CF_DNS_API_TOKEN:-}" ] || fallo "Falta el token de Cloudflare (Ajustes › Secretos)."
      [ -n "$LE_EMAIL" ] || fallo "Falta el correo para avisos de Let's Encrypt (Certificado › correo)."
      start_colima
      local hosts=(events.adventist.club admin.adventist.club api.adventist.club media.adventist.club)
      say "Pidiendo el certificado (necesita internet; 1–3 min)…"
      LE_EMAIL="$LE_EMAIL" bash "$NODO_DIR/tls/certificado-letsencrypt.sh" "${@:2}" "${hosts[@]}" 2>&1 | grep -v '^ *$' | tail -25
      [ "${PIPESTATUS[0]}" = 0 ] || fallo "No se obtuvo el certificado (¿internet? ¿token con permiso DNS Edit?)"
      ;;
    local)
      cert_local_crear || fallo "No se pudo crear el certificado local"
      ;;
  esac
  caddy_reiniciar
  ok "Certificado listo"
}

# ---------------------------------------------------------------------------------------------
# Red del evento: modo elegido, DNS de esta Mac («Mi red») y las revisiones.
#
# DNS de esta Mac: Colima/Lima NO reenvía UDP al anfitrión (su reenviador de puertos va por SSH,
# sólo TCP; probado con colima 0.10 / lima 2.2, vz), así que el DNS no va en un contenedor: lo
# sirve el ejecutable de la app (`Nodo Camporee --dns`, DNS.swift) como LaunchAgent del usuario.
# macOS deja a un proceso normal escuchar en el puerto 53 si escucha en todas las direcciones
# (0.0.0.0): no hace falta root ni ayudantes privilegiados.
DNS_PUERTO="${NODO_DNS_PUERTO:-53}"   # las pruebas usan otro puerto
ROUTER_DNS_PUERTO="${NODO_ROUTER_DNS_PUERTO:-53}"   # sólo pruebas: el «router» es un DNS en otro puerto

# ¿El DNS de esta Mac contesta events.adventist.club con una IP de ESTA Mac? Si contesta otra cosa,
# otra app intercepta el DNS de la Mac (VPN como NordVPN/Proton, Little Snitch…): DNS_INTERCEPTADO=1.
DNS_INTERCEPTADO=0
dns_responde() {
  local r; DNS_INTERCEPTADO=0
  r="$(dig +short +time=1 +tries=1 @127.0.0.1 -p "$DNS_PUERTO" events.adventist.club A 2>/dev/null | grep -E '^[0-9.]+$' | head -1)"
  [ -n "$r" ] || return 1
  if ifconfig 2>/dev/null | grep -qE "inet $(printf '%s' "$r" | sed 's/\./\\./g') "; then return 0; fi
  DNS_INTERCEPTADO=1; return 1
}

cmd_dns() {
  case "${1:-}" in
    on)
      local ip="" nombres="events.adventist.club api.adventist.club admin.adventist.club media.adventist.club nodo.local"
      [ "${2:-}" = --ip ] && ip="${3:-}"
      [ -x "$APP_EXE" ] || fallo "No encuentro la app en $APP_BUNDLE (el DNS lo corre la app)."
      local extra=""; [ -n "$ip" ] && extra="<string>--ip</string><string>$(xml "$ip")</string>"
      write_plist dns <<EOF
<key>ProgramArguments</key><array><string>$(xml "$APP_EXE")</string><string>--dns</string><string>--puerto</string><string>$DNS_PUERTO</string><string>--nombres</string><string>$(xml "$nombres")</string><string>--reenviar</string><string>$(xml "$DNS_REENVIAR")</string>$extra</array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$(xml "$RUN_DIR/dns.log")</string>
<key>StandardErrorPath</key><string>$(xml "$RUN_DIR/dns.log")</string>
EOF
      agent_load dns || fallo "No se pudo arrancar el DNS de esta Mac"
      local i; for i in 1 2 3 4 5 6; do dns_responde && break; sleep 1; done
      if dns_responde; then
        ok "DNS de esta Mac encendido (puerto $DNS_PUERTO): events/api/admin/media.adventist.club → esta Mac; lo demás → ${DNS_REENVIAR}"
      else
        tail -3 "$RUN_DIR/dns.log" 2>/dev/null
        [ "$DNS_INTERCEPTADO" = 1 ] && fallo "Otra app de esta Mac intercepta el DNS (una VPN como NordVPN o Proton, Little Snitch…): ciérrala o desactiva su «protección DNS» durante el evento."
        fallo "El DNS de esta Mac no responde (¿otro programa usa el puerto $DNS_PUERTO? ¿el Firewall lo bloquea?)"
      fi ;;
    off) agent_unload dns; ok "DNS de esta Mac apagado" ;;
    estado)
      local act=0 resp=0; agent_loaded dns && act=1; [ $act = 1 ] && dns_responde && resp=1
      printf '{"activo":%s,"responde":%s,"puerto":%s}\n' "$(jb $act)" "$(jb $resp)" "$DNS_PUERTO" ;;
    *) fallo "uso: dns-on [--ip IP] | dns-off | dns-estado" ;;
  esac
}

cmd_red_modo() {
  case "${1:-}" in glinet|google|otro|lugar|publica) ;; *) fallo "uso: red-modo glinet|google|otro|lugar|publica" ;; esac
  config_set RED_MODO "$1"
  config_set RED_REVISION ""; config_set RED_REVISION_T ""; config_set RED_REVISION_TXT ""
  # Con el GL.iNet el router ya responde los nombres del evento: el DNS de la Mac sobra.
  { [ "$1" = glinet ] || [ "$1" = publica ]; } && agent_loaded dns && { agent_unload dns; say "DNS de esta Mac apagado (no hace falta con el GL.iNet)"; }
  ok "Red del evento: $1"
}

# Equipos que esta Mac ve en la red (además de ella y del router): pista de «aislamiento de
# clientes» (redes de invitados). ping a «todos los equipos» (224.0.0.1) + tabla ARP.
equipos_visibles() {
  local yo="$1" router="$2" ifc="$3"
  local bc=""; [ -n "$ifc" ] && bc="$(ifconfig "$ifc" 2>/dev/null | awk '/inet .* broadcast/{print $NF; exit}')"
  { ping -t 2 224.0.0.1 2>/dev/null | grep -oE 'from [0-9.]+' | awk '{print $2}'
    [ -n "$bc" ] && ping -t 2 "$bc" 2>/dev/null | grep -oE 'from [0-9.]+' | awk '{print $2}'
    [ -n "$ifc" ] && arp -an -i "$ifc" 2>/dev/null | grep -v incomplete | grep -oE '\(([0-9.]+)\)' | tr -d '()'
  } | grep -vxF "$yo" | grep -vxF "$router" | grep -vE '\.255$|^224\.|^169\.254\.' | sort -u | wc -l | tr -d ' '
}

hay_internet() {
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 http://connectivitycheck.gstatic.com/generate_204 2>/dev/null)" = 204 ]
}

dns_mac_necesario() { [ "$RED_MODO" = google ] || { [ "$RED_MODO" = otro ] && [ "$ROUTER_TIPO" = dns ]; }; }

# Mide la red. Deja: R_IP R_ROUTER R_DNS_EV R_DNS_API R_DNS_OK R_DNS_MAC R_INTERNET R_EQUIPOS
red_medir() {
  detect_net
  R_ROUTER="$ROUTER_IP"; R_IP="$NET_IP"
  [ "$RED_MODO" = glinet ] || [ -z "$RED_MODO" ] && [ -n "$IP_FIJA" ] && [ "$NET_EN_ROUTER" = 1 ] && R_IP="$IP_FIJA"
  R_DNS_EV="$(dig +short +time=2 +tries=1 -p "$ROUTER_DNS_PUERTO" "@$R_ROUTER" events.adventist.club A 2>/dev/null | grep -E '^[0-9.]+$' | tail -1)"
  R_DNS_API="$(dig +short +time=2 +tries=1 -p "$ROUTER_DNS_PUERTO" "@$R_ROUTER" api.adventist.club A 2>/dev/null | grep -E '^[0-9.]+$' | tail -1)"
  R_DNS_OK=0; [ -n "$R_IP" ] && [ "$R_DNS_EV" = "$R_IP" ] && [ "$R_DNS_API" = "$R_IP" ] && R_DNS_OK=1
  R_DNS_MAC=0; agent_loaded dns && dns_responde && R_DNS_MAC=1
  R_INTERNET=0; hay_internet && R_INTERNET=1
  R_EQUIPOS="$(equipos_visibles "$NET_IP" "$R_ROUTER" "$NET_IFACE")"
}

cmd_red_revisar() {
  local total=4 ok_dns=1
  paso "1/$total Esta Mac en la red"
  red_medir
  if [ -z "$R_IP" ]; then fallo "Esta Mac no está conectada a ninguna red."; fi
  say "Esta Mac: $R_IP ($NET_IFACE, $( [ "$NET_KIND" = ethernet ] && echo cable || echo Wi-Fi )) · router: $R_ROUTER"
  paso "2/$total DNS a través del router"
  if [ "$R_DNS_OK" = 1 ]; then ok "El router responde events.adventist.club y api.adventist.club → $R_IP (esta Mac)"
  else aviso "El router responde events.adventist.club → ${R_DNS_EV:-nada} y api → ${R_DNS_API:-nada} (debería ser $R_IP)"; ok_dns=0; fi
  if dns_mac_necesario; then
    if [ "$R_DNS_MAC" = 1 ]; then ok "El DNS de esta Mac está encendido y responde"
    elif [ "$DNS_INTERCEPTADO" = 1 ]; then aviso "Otra app intercepta el DNS de esta Mac (VPN como NordVPN/Proton, Little Snitch…): ciérrala durante el evento"; ok_dns=0
    else aviso "El DNS de esta Mac está apagado: pulsa «Encender el DNS de esta Mac»"; ok_dns=0; fi
  fi
  paso "3/$total Otros equipos (aislamiento)"
  if [ "$R_EQUIPOS" -gt 0 ]; then ok "Veo $R_EQUIPOS equipo(s) más en la red: los teléfonos podrán llegar a esta Mac"
  else aviso "No veo otros equipos. Si un teléfono conectado no abre el sitio, la red aísla a los equipos (red de invitados): usa la red principal o tu GL.iNet."; fi
  paso "4/$total Internet"
  if [ "$R_INTERNET" = 1 ]; then ok "Hay internet en esta red"; else say "Sin internet en esta red (el nodo funciona igual)"; fi
  local bloqueo="" portal=""
  if [ "$RED_MODO" = publica ]; then
    # Red pública: un dominio cualquiera NO debe resolver (sumidero) y la página de portal responde.
    bloqueo="$(dig +short +time=2 +tries=1 "@$R_ROUTER" example.com A 2>/dev/null | grep -E '^[0-9.]+$' | tail -1)"
    if [ -z "$bloqueo" ] || [ "$bloqueo" = 0.0.0.0 ]; then ok "Otros sitios bloqueados (example.com → ${bloqueo:-nada})"
    else aviso "example.com → $bloqueo: el router NO está bloqueando los demás sitios"; ok_dns=0; fi
    if [ "${RED_PORTAL:-exito}" != ninguno ]; then
      portal="$(dig +short +time=2 +tries=1 "@$R_ROUTER" captive.apple.com A 2>/dev/null | tail -1)"
      local cuerpo; cuerpo="$(curl -s --max-time 4 -H 'Host: captive.apple.com' "http://$R_IP/hotspot-detect.html" 2>/dev/null)"
      if [ "$portal" = "$R_IP" ] && [ -n "$cuerpo" ]; then ok "Prueba «¿hay internet?» de los teléfonos: la contesta esta Mac (${RED_PORTAL:-exito})"
      else aviso "La prueba de «¿hay internet?» no llega a esta Mac (captive.apple.com → ${portal:-nada}; ¿nodo encendido?)"; fi
    fi
    [ "$NET_KIND" = ethernet ] || aviso "La Mac va por Wi-Fi: con el aislamiento de clientes los teléfonos no la verán. Conéctala por cable."
  fi
  local txt
  if [ $ok_dns = 1 ]; then txt="Listo: events.adventist.club → $R_IP"; else txt="Falta: el DNS no apunta a esta Mac ($R_IP)"; fi
  txt="$txt · internet: $( [ "$R_INTERNET" = 1 ] && echo sí || echo no ) · otros equipos: $R_EQUIPOS"
  config_set RED_REVISION "$( [ $ok_dns = 1 ] && echo ok || echo falla )"
  config_set RED_REVISION_T "$(date +%s)"
  config_set RED_REVISION_TXT "$txt"
  printf '@@json {"ok":%s,"modo":%s,"ip":%s,"router":%s,"dns_events":%s,"dns_api":%s,"dns_mac":%s,"dns_mac_necesario":%s,"internet":%s,"equipos":%s,"texto":%s}\n' \
    "$(jb $ok_dns)" "$(json_str "$RED_MODO")" "$(json_str "$R_IP")" "$(json_str "$R_ROUTER")" "$(json_str "$R_DNS_EV")" "$(json_str "$R_DNS_API")" \
    "$(jb "$R_DNS_MAC")" "$(dns_mac_necesario && echo true || echo false)" "$(jb "$R_INTERNET")" "$R_EQUIPOS" "$(json_str "$txt")"
  if [ $ok_dns = 1 ]; then ok "$txt"; else printf '@@error %s\n' "$txt"; return 1; fi
}

cmd_red_diagnostico() {
  paso "1/3 Mirando esta red"
  red_medir
  [ -n "$R_IP" ] || fallo "Esta Mac no está conectada a ninguna red: conéctala al Wi-Fi o al cable del lugar."
  say "Esta Mac: $R_IP · router: $R_ROUTER"
  paso "2/3 Internet, DNS y aislamiento"
  say "Internet: $( [ "$R_INTERNET" = 1 ] && echo sí || echo no )"
  say "Otros equipos visibles: $R_EQUIPOS"
  say "El router dice events.adventist.club → ${R_DNS_EV:-(sin respuesta)}"
  local glinet=0; router_is_glinet && glinet=1
  paso "3/3 Recomendación"
  local reco txt
  if [ "$R_DNS_OK" = 1 ]; then
    reco=listo; txt="Esta red ya manda events.adventist.club a esta Mac. Pulsa «Revisar» con un teléfono conectado."
  elif [ "$glinet" = 1 ]; then
    reco=glinet; txt="Estás en un GL.iNet: usa la opción «GL.iNet portátil»."
  elif [ "$R_EQUIPOS" = 0 ]; then
    reco=glinet; txt="Usa tu GL.iNet. No veo otros equipos: o esta red los aísla (como una red de invitados) y los teléfonos no llegarían a esta Mac, o todavía no hay nadie conectado (conecta un teléfono a esta red y vuelve a probar). Conecta el cable de esta red al puerto WAN del GL.iNet (así tiene internet si lo hay) y los teléfonos al Wi-Fi del GL.iNet."
  elif [ "$R_INTERNET" = 1 ]; then
    reco=nube; txt="Usa la nube. Esta red tiene internet: si es estable para todos, los teléfonos pueden usar events.adventist.club normal y el nodo queda de respaldo. Para usar el nodo en esta red, configura el DNS en tu router (Google Wifi u otro) o usa tu GL.iNet."
  else
    reco=dns_router; txt="Configura el DNS en tu router. Esta red no tiene internet: si administras el router, pon su DNS personalizado en esta Mac (opciones «Google Wifi» u «Otro router»). Si no lo administras, usa tu GL.iNet."
  fi
  printf '@@json {"recomendacion":%s,"texto":%s,"internet":%s,"equipos":%s,"dns_events":%s,"ip":%s,"router":%s}\n' \
    "$(json_str "$reco")" "$(json_str "$txt")" "$(jb "$R_INTERNET")" "$R_EQUIPOS" "$(json_str "$R_DNS_EV")" "$(json_str "$R_IP")" "$(json_str "$R_ROUTER")"
  ok "$txt"
}

# ---------------------------------------------------------------------------------------------
cmd_estado() {
  local docker_up=0 colima_up=0 st_db=absent st_api=absent st_web=absent st_caddy=absent st_resp=absent
  local https_local=0 https_lan=0 api_ok=0 web_ok=0
  if [ -S "$DOCKER_SOCK" ] && docker_ok; then docker_up=1; colima_up=1
    st_db="$(service_state db)"; st_api="$(service_state api)"; st_web="$(service_state web)"
    st_caddy="$(service_state caddy)"; st_resp="$(service_state respaldo)"
  fi
  detect_net
  local router_ok=0 glinet=0
  if router_alive; then router_ok=1; [ "$NET_EN_ROUTER" = 1 ] && router_is_glinet && glinet=1; fi
  if [ "$st_caddy" = running ] || [ "$st_caddy" = healthy ]; then
    https_local events.adventist.club /nodo-salud && https_local=1
    [ $https_local = 1 ] && https_local events.adventist.club /auth/login && web_ok=1
    [ $https_local = 1 ] && https_local api.adventist.club /api/v1/health && api_ok=1
    [ -n "$NET_IP" ] && https_get events.adventist.club /nodo-salud "$NET_IP" && https_lan=1
  fi
  cert_info
  # Respaldos
  local last="" last_t=0 usb="" usb_last="" usb_last_t=0 usb_on=0
  if last="$(last_dump "$RESPALDO_LOCAL")"; then last_t="${last#*|}"; last="${last%|*}"; fi
  if usb="$(usb_dir)"; then usb_on=1; if usb_last="$(last_dump "$usb")"; then usb_last_t="${usb_last#*|}"; usb_last="${usb_last%|*}"; fi; fi
  # Teléfonos: con Colima todas las conexiones llegan a Caddy desde la misma IP interna, así que
  # se cuentan los equipos que la Mac vio en la red del router (tabla ARP, ~20 min) y las
  # peticiones del registro de Caddy de los últimos 10 min (sin las comprobaciones de salud).
  local phones=0 reqs=0
  if [ "$NET_EN_ROUTER" = 1 ] && [ -n "$NET_IFACE" ]; then
    phones="$(arp -an -i "$NET_IFACE" 2>/dev/null | grep -v incomplete | grep -oE '\(([0-9.]+)\)' | tr -d '()' \
      | grep -vxF "$ROUTER_IP" | grep -vxF "$NET_IP" | grep -E "^$(router_prefix | sed 's/\./\\./g')" | grep -vE '\.255$' | sort -u | wc -l | tr -d ' ')"
  fi
  if [ -s "$NODO_DIR/datos/caddy/acceso.log" ]; then
    # Con la puerta, el registro JSON de Caddy trae la IP real de cada teléfono.
    local ips; ips="$(acceso_ips 600)"
    reqs="$(printf '%s\n' "$ips" | grep -c . | tr -d ' ')"
    if puerta_activa; then phones="$(printf '%s\n' "$ips" | grep . | sort -u | wc -l | tr -d ' ')"; fi
  elif [ "$st_caddy" = running ] || [ "$st_caddy" = healthy ]; then
    reqs="$(docker logs --since 10m "$(container caddy)" 2>&1 | grep 'handled request' | grep -vc '"curl/' | tr -d ' ')"
  fi
  # Energía
  local batt; batt="$(pmset -g batt 2>/dev/null)"
  local on_batt=0 pct=""; printf '%s' "$batt" | grep -q "Battery Power" && on_batt=1
  pct="$(printf '%s' "$batt" | grep -oE '[0-9]+%' | head -1 | tr -d %)"
  local awake=0; agent_loaded despierto && awake=1
  local mirror=0; agent_loaded respaldo-usb && mirror=1
  local autostart=0; [ -f "$(agent_plist autoarranque)" ] && autostart=1
  local free_gb; free_gb="$(df -g "$HOME" | awk 'NR==2{print $4}')"
  local fw; fw="$(/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null | grep -q 'enabled' && echo 1 || echo 0)"
  local imgs=0; [ $docker_up = 1 ] && docker image inspect "nodo-evento-api:${NODO_TAG:-local}" "nodo-evento-web:${NODO_TAG:-local}" >/dev/null 2>&1 && imgs=1
  local encendido=""; [ -f "$RUN_DIR/encendido" ] && encendido="$(cat "$RUN_DIR/encendido")"
  local espejo=""; [ -f "$RUN_DIR/ultimo-espejo" ] && espejo="$(cat "$RUN_DIR/ultimo-espejo")"
  local ip_cambio=0; [ -n "$IP_FIJA" ] && [ "$NET_EN_ROUTER" = 1 ] && [ "$NET_IP" != "$IP_FIJA" ] && ip_cambio=1
  printf '{'
  printf '"ahora":%s,' "$(date +%s)"
  printf '"docker":%s,"colima":%s,"imagenes":%s,"encendido_desde":%s,' "$(jb $docker_up)" "$(jb $colima_up)" "$(jb $imgs)" "$(json_str "$encendido")"
  printf '"servicios":{"db":"%s","api":"%s","web":"%s","caddy":"%s","respaldo":"%s"},' "$st_db" "$st_api" "$st_web" "$st_caddy" "$st_resp"
  printf '"https_local":%s,"https_lan":%s,"web_ok":%s,"api_ok":%s,' "$(jb $https_local)" "$(jb $https_lan)" "$(jb $web_ok)" "$(jb $api_ok)"
  # Wi-Fi con «dirección privada» rotativa: la MAC actual no es la del hardware (la reserva de IP
  # del router dejaría de servir). Con «Fija» también difiere, pero es estable: sólo es un aviso.
  local mac_hw=""; [ -n "$NET_IFACE" ] && mac_hw="$(networksetup -getmacaddress "$NET_IFACE" 2>/dev/null | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)"
  local mac_priv=0; [ -n "$mac_hw" ] && [ -n "$NET_MAC" ] && [ "$mac_hw" != "$NET_MAC" ] && mac_priv=1
  printf '"red":{"interfaz":%s,"tipo":%s,"ip":%s,"mac":%s,"en_red_router":%s,"router_responde":%s,"glinet":%s,"ip_fija":%s,"ip_cambio":%s,"router":%s,"gateway":%s,"mac_hw":%s,"mac_privada":%s},' \
    "$(json_str "$NET_IFACE")" "$(json_str "$NET_KIND")" "$(json_str "$NET_IP")" "$(json_str "$NET_MAC")" "$(jb "$NET_EN_ROUTER")" "$(jb $router_ok)" "$(jb $glinet)" "$(json_str "$IP_FIJA")" "$(jb $ip_cambio)" \
    "$(json_str "$ROUTER_IP")" "$(json_str "$GATEWAY")" "$(json_str "$mac_hw")" "$(jb $mac_priv)"
  local dns_act=0 dns_resp=0; agent_loaded dns && dns_act=1; [ $dns_act = 1 ] && dns_responde && dns_resp=1
  printf '"dns":{"activo":%s,"responde":%s,"interceptado":%s,"puerto":%s},' "$(jb $dns_act)" "$(jb $dns_resp)" "$(jb "$DNS_INTERCEPTADO")" "$DNS_PUERTO"
  # Alcance de este nodo (lo guarda «nodo-info» al preparar/subir/encender; null si nunca se preparó).
  local info="null"; [ -s "$RUN_DIR/nodo-info.json" ] && info="$(tr -d '\n' < "$RUN_DIR/nodo-info.json")"
  printf '"nodo":%s,' "$info"
  printf '"certificado":{"existe":%s,"tipo":%s,"vence":%s,"dias":%s,"cubre_evento":%s,"nombres":%s},' \
    "$(jb $CERT_EXISTE)" "$(json_str "$CERT_TIPO")" "$(json_str "$CERT_VENCE")" "${CERT_DIAS:-null}" "$(jb $CERT_CUBRE_EVENTO)" "$(json_str "$CERT_NOMBRES")"
  printf '"respaldo":{"local_dir":%s,"ultimo":%s,"ultimo_t":%s,"usb":%s,"usb_dir":%s,"usb_ultimo":%s,"usb_ultimo_t":%s,"espejo_activo":%s,"ultimo_espejo":%s,"cada_min":%s},' \
    "$(json_str "$RESPALDO_LOCAL")" "$(json_str "$(basename "${last:-}" 2>/dev/null)")" "$last_t" "$(jb $usb_on)" "$(json_str "$usb")" "$(json_str "$(basename "${usb_last:-}" 2>/dev/null)")" "$usb_last_t" "$(jb $mirror)" "$(json_str "$espejo")" "$RESPALDO_CADA_MIN"
  printf '"telefonos":{"ips_10min":%s,"peticiones_10min":%s},' "$phones" "$reqs"
  printf '"energia":{"bateria":%s,"porcentaje":%s,"despierto":%s},' "$(jb $on_batt)" "${pct:-null}" "$(jb $awake)"
  printf '"disco_libre_gb":%s,"firewall":%s,"autoarranque":%s,' "${free_gb:-0}" "$(jb "$fw")" "$(jb $autostart)"
  local pend=0 motivo="" inst="" constr=0
  motivo="$(instalacion_pendiente)" && pend=1
  if [ "$PAQUETE" = 1 ] && [ $docker_up = 1 ] && [ $imgs = 0 ]; then pend=1; motivo="${motivo:-faltan las imágenes}"; fi
  [ -f "$APP_SUPPORT/instalado" ] && inst="$(cat "$APP_SUPPORT/instalado")"
  puede_construir && constr=1
  printf '"instalacion":{"paquete":%s,"pendiente":%s,"motivo":%s,"version_paquete":%s,"version_instalada":%s,"herramientas":%s,"puede_construir":%s},' \
    "$(jb $PAQUETE)" "$(jb $pend)" "$(json_str "$motivo")" "$(json_str "$PAQUETE_VERSION")" "$(json_str "$inst")" "$(json_str "$HERR_EN_USO")" "$(jb $constr)"
  local puerta_on=0; agent_loaded puerta && puerta_on=1
  local malla_on=0; agent_loaded malla && malla_on=1
  printf '"puerta":{"activa":%s,"configurada":%s,"interno_https":%s},"malla":{"activa":%s},' "$(jb $puerta_on)" "$(jb "$( puerta_activa && echo 1 || echo 0)")" "$PUERTO_INTERNO_HTTPS" "$(jb $malla_on)"
  printf '"imagenes_version":{"requerida":%s,"cargada":%s},' "$(json_str "${PAQUETE_IMAGENES_VERSION:-}")" "$(json_str "$(cat "$RUN_DIR/imagenes-version" 2>/dev/null)")"
  printf '"config":%s' "$(config_json)"
  printf '}\n'
}

cmd_registros() {
  need_node_dir
  local svc="${1:-}"
  {
    if [ -n "$svc" ]; then docker logs --tail 150 "$(container "$svc")" 2>&1
    else for svc in db migrate api web caddy respaldo; do printf '===== %s =====\n' "$svc"; docker logs --tail 25 "$(container "$svc")" 2>&1; done; fi
    [ -f "$RUN_DIR/respaldo-usb.log" ] && { printf '===== copia a USB =====\n'; tail -10 "$RUN_DIR/respaldo-usb.log"; }
  } | depurar
}

# ---------------------------------------------------------------------------------------------
# «Ver sitio local»: abre en ESTA Mac el mismo events.adventist.club que ven los teléfonos, pero
# servido por el nodo. Navegador Chromium con perfil aparte y resolución de nombres forzada a esta
# Mac (no toca /etc/hosts ni el navegador de siempre).
cmd_ver_sitio() {
  local app="" a perfil="$APP_SUPPORT/navegador-local" ip="127.0.0.1"
  for a in "Google Chrome" "Brave Browser" "Microsoft Edge" "Chromium"; do
    if [ -d "/Applications/$a.app" ]; then app="$a"; break; fi
  done
  [ -n "$app" ] || fallo "Para ver el sitio local en esta Mac instala Google Chrome o Brave."
  mkdir -p "$perfil"
  open -na "$app" --args --user-data-dir="$perfil" --no-first-run --no-default-browser-check \
    --host-resolver-rules="MAP events.adventist.club $ip,MAP admin.adventist.club $ip,MAP api.adventist.club $ip,MAP media.adventist.club $ip" \
    "https://events.adventist.club/"
  ok "Abriendo el sitio local en $app (ventana aparte que apunta a esta Mac)"
}

# ---------------------------------------------------------------------------------------------
# v3 · Puerta (IP real), dispositivos, bloqueos, cuentas, Wi-Fi, nodos vecinos e imágenes
puerta_activa() { [ "$PUERTA" = 1 ] && [ -x "$APP_EXE" ]; }

# IPs de los clientes en el registro JSON de Caddy (últimos N segundos; sin la Mac ni Docker).
acceso_ips() {  # acceso_ips segundos
  local desde=$(( $(date +%s) - ${1:-600} ))
  tail -n 40000 "$NODO_DIR/datos/caddy/acceso.log" 2>/dev/null | awk -v t="$desde" '
    match($0, /"ts":[0-9.]+/) { ts = substr($0, RSTART + 5, RLENGTH - 5) + 0 }
    ts >= t && match($0, /"remote_ip":"[^"]+"/) { print substr($0, RSTART + 13, RLENGTH - 14) }' \
    | grep -vE '^(127\.|172\.(1[6-9]|2[0-9]|3[01])\.|::1$)' || true
}

preparar_bloqueos() {
  mkdir -p "$NODO_DIR/bloqueos" "$NODO_DIR/datos/caddy"
  [ -f "$NODO_DIR/bloqueos/bloqueados.caddy" ] || printf '# Sin bloqueos (la app «Nodo Camporee» reescribe este archivo).\n' > "$NODO_DIR/bloqueos/bloqueados.caddy"
  [ -f "$RUN_DIR/bloqueados.json" ] || printf '[]\n' > "$RUN_DIR/bloqueados.json"
}

start_puerta() {
  if [ "$PUERTA" != 1 ]; then agent_unload puerta; say "Puerta apagada en la configuración: los teléfonos se ven con una sola IP"; return 0; fi
  [ -x "$APP_EXE" ] || { aviso "No encuentro la app ($APP_BUNDLE): sin la puerta, los teléfonos se ven con una sola IP"; return 0; }
  local pares="${NODO_PUERTA_PARES:-80:$PUERTO_INTERNO_HTTP 443:$PUERTO_INTERNO_HTTPS}" args="" par
  for par in $pares; do args="$args<string>--par</string><string>$par</string>"; done
  write_plist puerta <<PL
<key>ProgramArguments</key><array><string>$(xml "$APP_EXE")</string><string>--puerta</string>$args<string>--bloqueados</string><string>$(xml "$RUN_DIR/bloqueados.json")</string><string>--estado</string><string>$(xml "$RUN_DIR/puerta.json")</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$(xml "$RUN_DIR/puerta.log")</string>
<key>StandardErrorPath</key><string>$(xml "$RUN_DIR/puerta.log")</string>
PL
  agent_load puerta || { aviso "No se pudo arrancar la puerta (puertos 80/443)"; return 0; }
  local i ip; detect_net; ip="${NET_IP:-127.0.0.1}"
  for i in $(seq 1 10); do https_get events.adventist.club /nodo-salud "$ip" && break; sleep 1; done
  if https_get events.adventist.club /nodo-salud "$ip"; then ok "Puerta abierta en $ip: Caddy ve la IP real de cada teléfono"
  else aviso "La puerta no contesta en $ip:443. Si macOS o Little Snitch preguntan por «Nodo Camporee», pulsa Permitir («Revisar interferencias»)."; fi
}

# Bonjour de los nodos vecinos: lo hace la app (`--malla`), que anuncia este nodo en la red y escribe
# los vecinos encontrados en run/malla-vecinos.json (los lee el servicio «malla» del compose).
start_malla_bonjour() {
  [ -x "$APP_EXE" ] || return 0
  write_plist malla <<PL
<key>ProgramArguments</key><array><string>$(xml "$APP_EXE")</string><string>--malla</string><string>--run</string><string>$(xml "$RUN_DIR")</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$(xml "$RUN_DIR/malla-bonjour.log")</string>
<key>StandardErrorPath</key><string>$(xml "$RUN_DIR/malla-bonjour.log")</string>
PL
  agent_load malla && ok "Nodos vecinos: este nodo se anuncia en la red (Bonjour)" || aviso "No se pudo anunciar el nodo en la red (nodos vecinos)"
}

contenedor_arriba() { [ "$(service_state "$1")" = healthy ] || [ "$(service_state "$1")" = running ]; }

cmd_caddy_recargar() {
  need_node_dir
  contenedor_arriba caddy || { ok "Caddy apagado: los bloqueos se aplican al encender"; return 0; }
  if docker exec "$(container caddy)" caddy reload --config /etc/caddy/Caddyfile --address localhost:2019 >/dev/null 2>&1; then
    ok "Bloqueos aplicados en el nodo"
  else
    # Instalaciones con el Caddyfile viejo (sin admin): reinicio corto.
    docker restart "$(container caddy)" >/dev/null 2>&1 && ok "Bloqueos aplicados (Caddy reiniciado)" || fallo "No pude aplicar los bloqueos en Caddy"
  fi
}

# Llamadas del operador a la API del nodo (dentro del contenedor: el token nunca sale de ahí).
api_operador() {  # api_operador METODO RUTA [json]
  contenedor_arriba api || fallo "La API del nodo está apagada"
  docker exec -i "$(container api)" python - "$1" "$2" "${3:-}" <<'PY'
import json, os, sys, urllib.request, urllib.error
metodo, ruta, cuerpo = sys.argv[1], sys.argv[2], sys.argv[3]
tok = os.environ.get("NODO_OPERADOR_TOKEN", "")
if not tok:
    print(json.dumps({"error": "La API del nodo no tiene NODO_OPERADOR_TOKEN (apaga y enciende el nodo)"})); sys.exit(3)
req = urllib.request.Request("http://127.0.0.1:8000/api/v1/nodo/operador" + ruta, method=metodo,
                             data=cuerpo.encode() if cuerpo else None,
                             headers={"X-Nodo-Operador": tok, "Content-Type": "application/json"})
try:
    print(urllib.request.urlopen(req, timeout=8).read().decode())
except urllib.error.HTTPError as e:
    print(json.dumps({"error": f"HTTP {e.code}", "detalle": e.read().decode()[:300]})); sys.exit(1)
PY
}

cmd_dispositivos() {  # JSON: API (cuentas, fallidos) + ARP (MAC) — Caddy lo lee la app directo
  need_node_dir
  detect_net
  local api='{"dispositivos":[]}' arp
  contenedor_arriba api && api="$(api_operador GET /dispositivos 2>/dev/null | tail -1)"
  printf '%s' "$api" | grep -q '^{' || api='{"dispositivos":[]}'
  arp="$(arp -an ${NET_IFACE:+-i "$NET_IFACE"} 2>/dev/null)"
  printf '{"api":%s,"arp":%s,"acceso":%s,"mi_ip":%s,"router":%s,"gateway":%s}\n' "$api" "$(json_str "$arp")" \
    "$(json_str "$NODO_DIR/datos/caddy/acceso.log")" "$(json_str "$NET_IP")" "$(json_str "$ROUTER_IP")" "$(json_str "$GATEWAY")"
}

cmd_cuentas() {  # cuentas bloqueadas | cuentas desbloquear CORREO
  need_node_dir
  case "${1:-}" in
    bloqueadas) api_operador GET /cuentas/bloqueadas ;;
    desbloquear)
      local correo="${2:-}"; [ -n "$correo" ] || fallo "uso: cuentas desbloquear CORREO"
      local cuerpo; cuerpo="$(printf '{"email":%s}' "$(json_str "$correo")")"
      if api_operador POST /cuentas/desbloquear "$cuerpo" >/dev/null; then ok "Cuenta desbloqueada: $correo"; else fallo "No se pudo desbloquear $correo"; fi ;;
    *) fallo "uso: cuentas bloqueadas | cuentas desbloquear CORREO" ;;
  esac
}

# Clientes que conoce el router (DHCP): nombre del equipo y MAC. Necesita la contraseña del router
# (sólo en memoria). GL.iNet/OpenWrt: /tmp/dhcp.leases · MikroTik: lease print terse.
cmd_router_clientes() {
  case "$ROUTER_TIPO" in
    mikrotik) printf '%s\n' '/ip dhcp-server lease print terse' | router_ssh ;;
    *) printf '%s\n' 'cat /tmp/dhcp.leases 2>/dev/null; true' | router_ssh ;;
  esac
}

mac_valida() { printf '%s' "$1" | grep -qiE '^([0-9a-f]{2}:){5}[0-9a-f]{2}$'; }

# Bloqueo en el router por MAC (además del bloqueo del nodo, que siempre se hace en la app):
#   GL.iNet/OpenWrt: lista negra del Wi-Fi (uci, persistente) + expulsión inmediata con ban (ubus
#   hostapd) SIN reiniciar el Wi-Fi de los demás. Quitar un bloqueo sí recarga el Wi-Fi (unos segundos).
#   MikroTik: access-list (RouterOS 7 «wifi» y 6 «wireless») con comentario «nodo-bloqueo».
router_bloqueo_guion() {  # router_bloqueo_guion MAC poner|quitar
  local mac; mac="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  if [ "$ROUTER_TIPO" = mikrotik ]; then
    local M; M="$(printf '%s' "$mac" | tr '[:lower:]' '[:upper:]')"
    if [ "$2" = poner ]; then
      printf '%s\n' \
        ":do { /interface wifi access-list remove [find comment=\"nodo-bloqueo\" mac-address=\"$M\"] } on-error={}" \
        ":do { /interface wifi access-list add mac-address=$M action=reject comment=\"nodo-bloqueo\" place-before=0 } on-error={}" \
        ":do { /interface wifi registration-table remove [find mac-address=\"$M\"] } on-error={}" \
        ":do { /interface wireless access-list remove [find comment=\"nodo-bloqueo\" mac-address=\"$M\"] } on-error={}" \
        ":do { /interface wireless access-list add mac-address=$M authentication=no forwarding=no comment=\"nodo-bloqueo\" place-before=0 } on-error={}" \
        ":do { /interface wireless registration-table remove [find mac-address=\"$M\"] } on-error={}" \
        ":put \"router: $M bloqueado\""
    else
      printf '%s\n' \
        ":do { /interface wifi access-list remove [find comment=\"nodo-bloqueo\" mac-address=\"$M\"] } on-error={}" \
        ":do { /interface wireless access-list remove [find comment=\"nodo-bloqueo\" mac-address=\"$M\"] } on-error={}" \
        ":put \"router: $M desbloqueado\""
    fi
    return
  fi
  cat <<SH
set -e
MAC=$(sq "$mac"); n=0
for s in \$(uci -X show wireless | sed -n "s/^wireless\\.\\([^.=]*\\)=wifi-iface\$/\\1/p"); do
  [ "\$(uci -q get wireless.\$s.mode)" = ap ] || continue
  f="\$(uci -q get wireless.\$s.macfilter || true)"
  if [ "\$f" = allow ]; then echo "AVISO: \$s usa lista PERMITIDA (macfilter=allow): no la cambio"; continue; fi
  for v in \$(uci -q get wireless.\$s.maclist || true); do [ "\$(echo "\$v" | tr A-Z a-z)" = "\$MAC" ] && uci del_list wireless.\$s.maclist="\$v"; done
SH
  if [ "$2" = poner ]; then
    cat <<'SH'
  uci set wireless.$s.macfilter='deny'
  uci add_list wireless.$s.maclist="$MAC"
  n=$((n+1))
done
uci commit wireless
for o in $(ubus list 'hostapd.*' 2>/dev/null); do
  ubus call "$o" del_client "{\"addr\":\"$MAC\",\"reason\":5,\"deauth\":true,\"ban_time\":86400000}" >/dev/null 2>&1 || true
done
echo "router: $MAC bloqueado en $n redes Wi-Fi (expulsado ya; los demás no se desconectan)"
SH
  else
    cat <<'SH'
  [ -z "$(uci -q get wireless.$s.maclist || true)" ] && uci -q delete wireless.$s.macfilter || true
  n=$((n+1))
done
uci commit wireless
echo "router: $MAC desbloqueado (el Wi-Fi se recarga en 3 s; los teléfonos se reconectan solos)"
( sleep 3; wifi reload ) >/dev/null 2>&1 &
SH
  fi
}

cmd_router_bloqueo() {  # router-bloquear --mac MAC [--quitar] [--simular] [--tipo …]
  local mac="" modo=poner simular=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --mac) mac="$2"; shift ;;
      --quitar) modo=quitar ;;
      --simular) simular=1 ;;
      --tipo) ROUTER_TIPO="$2"; shift ;;
    esac
    shift
  done
  mac_valida "$mac" || fallo "MAC inválida: $mac"
  case "$ROUTER_TIPO" in glinet|openwrt|mikrotik) ;; *) fallo "Este router no se bloquea desde la app (Google Wifi: pausa el dispositivo en Google Home)." ;; esac
  if [ $simular = 1 ]; then router_bloqueo_guion "$mac" "$modo"; return 0; fi
  router_bloqueo_guion "$mac" "$modo" | router_ssh || fallo "El router no aceptó el cambio (¿contraseña?)"
}

# Contraseña del Wi-Fi (WPA2) en GL.iNet/OpenWrt/MikroTik. La nueva llega en WIFI_PASSWORD (la app
# la guarda en el Llavero para el QR). 8–63 caracteres imprimibles, sin comillas ni «\».
wifi_clave_valida() {
  local k="${WIFI_PASSWORD:-}"
  [ ${#k} -ge 8 ] && [ ${#k} -le 63 ] || return 1
  printf '%s' "$k" | LC_ALL=C grep -q '^[ -~]*$' || return 1
  case "$k" in *\"*|*\'*|*\\*|*\$*|*\`*) return 1 ;; esac
}

wifi_clave_guion() {
  if [ "$ROUTER_TIPO" = mikrotik ]; then
    printf '%s\n' \
      ":do { /interface wifi set [find] security.authentication-types=wpa2-psk,wpa3-psk security.passphrase=\"$WIFI_PASSWORD\" } on-error={}" \
      ":do { /interface wireless security-profiles set [find default=yes] mode=dynamic-keys authentication-types=wpa2-psk wpa2-pre-shared-key=\"$WIFI_PASSWORD\" } on-error={}" \
      ':put "router: contraseña del Wi-Fi cambiada (WPA2)"'
    return
  fi
  cat <<SH
set -e
KEY=$(sq "$WIFI_PASSWORD"); SSID=$(sq "${WIFI_SSID:-}"); n=0
for s in \$(uci -X show wireless | sed -n "s/^wireless\\.\\([^.=]*\\)=wifi-iface\$/\\1/p"); do
  [ "\$(uci -q get wireless.\$s.mode)" = ap ] || continue
  [ "\$(uci -q get wireless.\$s.network)" = lan ] || continue
  uci set wireless.\$s.encryption='psk2'
  uci set wireless.\$s.key="\$KEY"
  [ -n "\$SSID" ] && uci set wireless.\$s.ssid="\$SSID"
  n=\$((n+1))
done
uci commit wireless
echo "router: contraseña WPA2 puesta en \$n redes Wi-Fi (se recarga en 3 s: los teléfonos piden la clave nueva)"
( sleep 3; wifi reload ) >/dev/null 2>&1 &
SH
}

cmd_wifi_clave() {  # wifi-clave [--ssid S] [--simular] [--tipo …]   (WIFI_PASSWORD = la nueva)
  local simular=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --ssid) WIFI_SSID="$2"; shift ;;
      --simular) simular=1 ;;
      --tipo) ROUTER_TIPO="$2"; shift ;;
    esac
    shift
  done
  wifi_clave_valida || fallo "La contraseña debe tener de 8 a 63 caracteres, sin comillas, «\\», «\$» ni acentos."
  case "$ROUTER_TIPO" in glinet|openwrt|mikrotik) ;; *) fallo "En este router cámbiala en su app o panel (Google Home › Wi-Fi › Configuración de red)." ;; esac
  if [ $simular = 1 ]; then wifi_clave_guion | sed "s/$(printf '%s' "$WIFI_PASSWORD" | sed 's/[][\.*^$/]/\\&/g')/••••••••/g"; return 0; fi
  wifi_clave_guion | router_ssh || fallo "El router no aceptó el cambio (¿contraseña de administración?)"
  [ "$RED_MODO" = publica ] && config_set PUBLICA_ABIERTA 0 >/dev/null
  ok "Contraseña del Wi-Fi cambiada. El QR «Conéctate al Wi-Fi» ya usa la nueva."
}

# Nodos vecinos: órdenes del servicio «malla» (scripts/nodo_malla.py dentro del contenedor).
cmd_malla() {
  need_node_dir
  contenedor_arriba malla || fallo "Nodos vecinos: enciende el nodo primero."
  docker exec -i "$(container malla)" python scripts/nodo_malla.py "$@"
}

# ---------------------------------------------------------------------------------------------
# Imágenes de Docker versionadas aparte de la app (actualizaciones chicas):
#   imagenes.json (en la app completa o publicado junto a las actualizaciones) =
#   {"version": V, "archivos": [{"nombre", "imagen", "id", "sha256", "tam"}]}
#   Sólo se cargan las que faltan (docker image inspect → Id). Orden: caché local
#   (Application Support/imagenes/, se llena al «Instalar» del DMG) → la app (DMG) → internet
#   (IMAGENES_URL/V/, manifiesto firmado con la llave EdDSA de las actualizaciones).
IMG_CACHE="$APP_SUPPORT/imagenes"

imagen_id_local() { docker image inspect --format '{{.Id}}' "$1" 2>/dev/null; }
# ¿La imagen local es la del manifiesto? (Id del almacén de containerd o, en el clásico, la configuración.)
imagen_al_dia() {  # imagen id config
  local l; l="$(imagen_id_local "$1")"; [ -n "$l" ] && { [ "$l" = "$2" ] || [ "$l" = "${3:-}" ]; }
}

manifiesto_campos() {  # manifiesto_campos archivo.json → «nombre imagen id sha256 tam config» por línea (sin jq)
  /usr/bin/plutil -convert json -o - "$1" >/dev/null 2>&1 || return 1
  local n i
  n="$(/usr/bin/plutil -extract archivos raw -o - "$1" 2>/dev/null)" || return 1
  for i in $(seq 0 $((n - 1))); do
    printf '%s %s %s %s %s %s\n' \
      "$(/usr/bin/plutil -extract "archivos.$i.nombre" raw -o - "$1")" "$(/usr/bin/plutil -extract "archivos.$i.imagen" raw -o - "$1")" \
      "$(/usr/bin/plutil -extract "archivos.$i.id" raw -o - "$1")" "$(/usr/bin/plutil -extract "archivos.$i.sha256" raw -o - "$1")" \
      "$(/usr/bin/plutil -extract "archivos.$i.tam" raw -o - "$1" 2>/dev/null || echo 0)" \
      "$(/usr/bin/plutil -extract "archivos.$i.config" raw -o - "$1" 2>/dev/null || echo -)"
  done
}

cmd_imagenes_estado() {  # qué falta según un manifiesto (por defecto el de la app)
  local man="${1:-$RES_DIR/imagenes.json}"
  [ -s "$man" ] || { printf '{"manifiesto":false}\n'; return 0; }
  local ver faltan=0 bytes=0 nombre imagen id sha tam lista=""
  ver="$(/usr/bin/plutil -extract version raw -o - "$man" 2>/dev/null)"
  local cfg
  while read -r nombre imagen id sha tam cfg; do
    [ -n "$nombre" ] || continue
    if ! imagen_al_dia "$imagen" "$id" "$cfg"; then
      faltan=$((faltan + 1)); bytes=$((bytes + tam)); lista="$lista${lista:+,}$(json_str "$nombre")"
    fi
  done < <(manifiesto_campos "$man")
  printf '{"manifiesto":true,"version":%s,"faltan":%s,"bytes":%s,"archivos":[%s]}\n' "$(json_str "$ver")" "$faltan" "$bytes" "$lista"
}

sha256_de() { shasum -a 256 "$1" | cut -d' ' -f1; }

# Busca el archivo (por sha256) en la caché, en la app o lo descarga; lo deja en la caché.
obtener_archivo_imagen() {  # nombre sha256 tam version base_url
  local nombre="$1" sha="$2" ver="$4" base="$5" dest="$IMG_CACHE/$2-$1"
  mkdir -p "$IMG_CACHE"
  [ -s "$dest" ] && [ "$(sha256_de "$dest")" = "$sha" ] && { printf '%s' "$dest"; return 0; }
  local f
  for f in "$RES_DIR/imagenes/$nombre" "$RES_DIR/imagenes/${nombre%.gz}"; do
    if [ -s "$f" ] && [ "$(sha256_de "$f")" = "$sha" ]; then
      cp -c "$f" "$dest" 2>/dev/null || cp "$f" "$dest"; printf '%s' "$dest"; return 0
    fi
  done
  [ -n "$base" ] || return 1
  say "Descargando $nombre ($(( $3 / 1048576 )) MB)…" >&2
  curl -fL --retry 3 --connect-timeout 10 -C - -o "$dest.parcial" "$base/$ver/$nombre" >&2 || return 1
  [ "$(sha256_de "$dest.parcial")" = "$sha" ] || { rm -f "$dest.parcial"; aviso "El archivo $nombre llegó dañado (sha256)" >&2; return 1; }
  mv "$dest.parcial" "$dest"; printf '%s' "$dest"
}

# imagenes-actualizar [--manifiesto RUTA] [--base URL] [--simular]
#   El manifiesto viene DENTRO de la app (firmada por Apple y por Sparkle): los archivos se aceptan
#   sólo si su sha256 coincide, vengan de la caché, de la app o de internet (BASE/VERSIÓN/nombre).
cmd_imagenes_actualizar() {
  local man="$RES_DIR/imagenes.json" base="${NODO_IMAGENES_URL:-$IMAGENES_URL}" simular=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --manifiesto) man="$2"; shift ;;
      --base) base="$2"; shift ;;
      --simular) simular=1 ;;
    esac
    shift
  done
  [ -s "$man" ] || fallo "No hay manifiesto de imágenes"
  docker_ok || fallo "Docker apagado: pulsa «Encender» o «Instalar» primero"
  local ver; ver="$(/usr/bin/plutil -extract version raw -o - "$man" 2>/dev/null)"
  local nombre imagen id sha tam cfg n=0 f
  while read -r nombre imagen id sha tam cfg; do
    [ -n "$nombre" ] || continue
    if imagen_al_dia "$imagen" "$id" "$cfg"; then say "  $imagen: al día"; continue; fi
    if [ $simular = 1 ]; then say "  $imagen: falta ($nombre, $(( tam / 1048576 )) MB)"; n=$((n + 1)); continue; fi
    f="$(obtener_archivo_imagen "$nombre" "$sha" "$tam" "$ver" "$base")" || fallo "Falta $nombre y no hay de dónde sacarlo (sin internet: usa el DMG completo)"
    say "Cargando ${imagen}…"
    docker load -i "$f" 2>&1 | grep -E '^Loaded image' | sed 's/^/  /'
    imagen_al_dia "$imagen" "$id" "$cfg" || fallo "Después de cargar $nombre la imagen no coincide"
    n=$((n + 1))
  done < <(manifiesto_campos "$man")
  [ $simular = 1 ] && { ok "Simulación: $n imagen(es) por cargar (versión $ver)"; return 0; }
  printf '%s' "$ver" > "$RUN_DIR/imagenes-version"
  # Caché: sólo los archivos del manifiesto vigente (lo viejo se borra).
  local keep; keep="$(manifiesto_campos "$man" | awk '{print $4 "-" $1}')"
  for f in "$IMG_CACHE"/*; do [ -e "$f" ] || continue; printf '%s\n' "$keep" | grep -qxF "$(basename "$f")" || rm -f "$f"; done
  ok "Imágenes al día (versión $ver; $n cargada(s))"
}

case "${1:-}" in
  estado) cmd_estado ;;
  encender) cmd_encender ;;
  apagar) cmd_apagar ;;
  respaldar) cmd_respaldar ;;
  respaldo-espejo) cmd_respaldo_espejo ;;
  construir) cmd_construir ;;
  exportar) shift; cmd_sync export "$@" ;;
  importar) shift; cmd_sync import "$@" ;;
  sync-estado) shift; cmd_sync status "$@" ;;
  router-plan) shift; cmd_router plan "$@" ;;
  router-aplicar) shift; cmd_router aplicar "$@" ;;
  router-revisar) cmd_router_revisar ;;
  red-modo) shift; cmd_red_modo "$@" ;;
  red-revisar) cmd_red_revisar ;;
  red-diagnostico) cmd_red_diagnostico ;;
  dns-on) shift; cmd_dns on "$@" ;;
  red-publica-plan) shift; cmd_red_publica plan "$@" ;;
  red-publica-aplicar) shift; cmd_red_publica aplicar "$@" ;;
  red-publica-quitar) shift; cmd_red_publica quitar "$@" ;;
  red-publica-guion) shift; [ "${1:-}" = --tipo ] && ROUTER_TIPO="$2"; WIFI_SSID=""; router_target
    router_guion_aplicar "$ROUTER_T_IP" "$ROUTER_T_MAC"
    if [ "$ROUTER_TIPO" = mikrotik ]; then publica_script_mikrotik "$ROUTER_T_IP" aplicar; else publica_script_openwrt "$ROUTER_T_IP" aplicar; fi ;;  # sólo pruebas
  dns-off) cmd_dns off ;;
  dns-estado) cmd_dns estado ;;
  preparar-auto) shift; cmd_preparar_auto "$@" ;;
  subir-auto) shift; cmd_subir_auto "$@" ;;
  nodo-info) shift; cmd_nodo_info "$@" ;;
  router-guion) shift; [ "${1:-}" = --tipo ] && ROUTER_TIPO="$2"; router_target; router_guion_aplicar "$ROUTER_T_IP" "$ROUTER_T_MAC" ;;  # sólo pruebas (pruebas/router-openwrt.sh)
  cert-letsencrypt) shift; cmd_cert letsencrypt "$@" ;;
  cert-local) cmd_cert local ;;
  autoarranque) shift; cmd_autoarranque "$@" ;;
  config) shift; case "${1:-}" in json) config_json; echo ;; set) config_set "$2" "${3-}"; ok "guardado: $2" ;; *) fallo "uso: config json|set CLAVE VALOR" ;; esac ;;
  registros) shift; cmd_registros "$@" ;;
  ver-sitio) cmd_ver_sitio ;;
  instalar) cmd_instalar ;;
  caddy-recargar) cmd_caddy_recargar ;;
  dispositivos) cmd_dispositivos ;;
  cuentas) shift; cmd_cuentas "$@" ;;
  router-clientes) cmd_router_clientes ;;
  router-bloquear) shift; cmd_router_bloqueo "$@" ;;
  wifi-clave) shift; cmd_wifi_clave "$@" ;;
  malla) shift; cmd_malla "$@" ;;
  imagenes-estado) shift; cmd_imagenes_estado "$@" ;;
  imagenes-actualizar) shift; cmd_imagenes_actualizar "$@" ;;
  *) sed -n '2,42p' "$0"; exit 2 ;;
esac
