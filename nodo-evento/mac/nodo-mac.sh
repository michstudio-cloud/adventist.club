#!/usr/bin/env bash
# Motor de «Nodo Camporee» (la app de Mac). La app (SwiftUI) sólo dibuja botones y luces; todo lo
# que hace pasa por este script, que también se puede correr a mano para diagnosticar:
#
#   nodo-mac.sh estado                  JSON con todo lo que muestra el panel (nunca secretos)
#   nodo-mac.sh encender | apagar       levanta / detiene el nodo (Colima + docker compose)
#   nodo-mac.sh respaldar               respaldo ahora + copia a la USB
#   nodo-mac.sh respaldo-espejo         sólo copia los respaldos a la USB (lo llama un LaunchAgent)
#   nodo-mac.sh construir               construye las imágenes (CON internet, una vez)
#   nodo-mac.sh exportar [--dry-run]    producción -> nodo (víspera, con internet)
#   nodo-mac.sh importar [--dry-run] [--prefer node|prod] [--keep-lock]   nodo -> producción
#   nodo-mac.sh sync-estado             qué tiene cada lado
#   nodo-mac.sh router-plan|router-aplicar [--simular]   configura el GL.iNet por SSH (uci)
#   nodo-mac.sh router-revisar          ¿el DNS del router dice que events.adventist.club es esta Mac?
#   nodo-mac.sh cert-letsencrypt [--staging] | cert-local
#   nodo-mac.sh autoarranque on|off
#   nodo-mac.sh config json | config set CLAVE VALOR
#   nodo-mac.sh registros [servicio]    últimas líneas de los registros
#   nodo-mac.sh instalar                versión portátil (DMG): copia el nodo, las herramientas y
#                                       carga las imágenes que trae la app (SIN internet)
#
# Secretos: NUNCA se leen de disco ni se escriben. La app los saca del Llavero de macOS y los pasa
# por el entorno sólo a las órdenes que los necesitan (POSTGRES_PASSWORD, JWT_SECRET,
# EVENT_PASS_SECRET, PROXY_SHARED_SECRET, PROD_DATABASE_URL, CF_DNS_API_TOKEN, NODO_ROUTER_PASS,
# WIFI_PASSWORD). Este script jamás los imprime.
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
DOMINIOS="events.adventist.club api.adventist.club"   # + admin.adventist.club si se quiere
WIFI_SSID=""
LE_EMAIL=""
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
# shellcheck disable=SC1090
[ -f "$CONFIG" ] && . "$CONFIG"
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
PROJECT="nodo-evento"

say()   { printf '%s\n' "$*"; }
paso()  { printf '@@paso %s\n' "$*"; }
ok()    { printf '@@ok %s\n' "$*"; }
aviso() { printf '@@aviso %s\n' "$*"; }
fallo() { printf '@@error %s\n' "$*"; exit 1; }

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
    *) fallo "clave de configuración desconocida: $key" ;;
  esac
  tmp="$(mktemp "$APP_SUPPORT/.config.XXXXXX")"
  { [ -f "$CONFIG" ] && grep -v "^${key}=" "$CONFIG"; printf '%s=%q\n' "$key" "$value"; } > "$tmp"
  chmod 600 "$tmp" && mv "$tmp" "$CONFIG"
}

config_json() {
  printf '{'
  local first=1 k
  for k in NODO_DIR FRONTEND_DIR EVENTO_ID ACTOR_EMAIL NODO_LABEL EVENTO_FIN ROUTER_IP IP_FIJA MAC_FIJA DOMINIOS WIFI_SSID LE_EMAIL COLIMA_PROFILE COLIMA_CPU COLIMA_MEM RESPALDO_CADA_MIN RESPALDO_LOCAL USB_LABEL; do
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
    printf 'NODO_HOSTS="events.adventist.club, admin.adventist.club, api.adventist.club"\n'
    printf 'WEB_BUILD_ID=%s\n' "$WEB_BUILD_ID"
  } > "$f"
  chmod 600 "$f"
  printf '%s' "$f"
}

# docker compose con los secretos que la app pasó por el entorno. Las órdenes que no los usan
# (down, ps) reciben un relleno: compose exige las variables para interpolar, aunque no las use.
dc() {
  local envf; envf="$(compose_env_file)"
  docker compose -p "$PROJECT" --project-directory "$NODO_DIR" -f "$NODO_DIR/docker-compose.yml" --env-file "$envf" "$@"
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
https_get() {  # https_get host path ip
  local ca=()
  [ -s "$NODO_DIR/tls/actual/ca-local.pem" ] && ca=(--cacert "$NODO_DIR/tls/actual/ca-local.pem")
  curl -fsS -o /dev/null --max-time 4 "${ca[@]}" --resolve "$1:443:$3" "https://$1$2" 2>/dev/null
}

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
    [ -x "$RES_DIR/herramientas/bin/colima" ] || fallo "La app no trae Colima/Docker y esta Mac no los tiene. Con internet: brew install colima docker docker-compose"
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

  paso "3/$total Encendiendo Docker (la primera vez crea la máquina virtual: 1–3 min)"
  start_colima

  paso "4/$total Cargando el sistema (sin internet; 1–3 min)"
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
  if ! docker image inspect "nodo-evento-api:local" "nodo-evento-web:local" >/dev/null 2>&1; then
    # Versión portátil: las imágenes vienen en la app (p. ej. si se borró la máquina de Docker).
    if [ "$PAQUETE" = 1 ]; then cargar_imagenes
    else fallo "Falta construir el sistema (Ajustes › «Construir», con internet, una sola vez)."; fi
  fi
  [ -s "$NODO_DIR/tls/actual/fullchain.pem" ] && [ -s "$NODO_DIR/tls/actual/privkey.pem" ] \
    || fallo "Falta el certificado HTTPS (botón «Certificado»)."
  mkdir -p "$NODO_DIR/datos/media" "$NODO_DIR/datos/exports" "$RESPALDO_LOCAL" || fallo "No puedo crear $RESPALDO_LOCAL"
  ok "Sistema listo"
  paso "4/$total Arrancando base de datos, API, sitio y respaldos (1–2 min)"
  local out
  if ! out="$(dc up -d --wait --wait-timeout 300 db migrate api web caddy respaldo 2>&1)"; then
    printf '%s\n' "$out" | grep -v '^ *$' | tail -15
    fallo "El nodo no arrancó completo. Botón «Registros» para ver el detalle."
  fi
  ok "Servicios arriba"
  paso "5/$total Comprobando el sitio por HTTPS"
  local i good=0
  for i in $(seq 1 20); do
    if https_get events.adventist.club /nodo-salud 127.0.0.1 && https_get api.adventist.club /api/v1/health 127.0.0.1; then good=1; break; fi
    sleep 2
  done
  [ $good = 1 ] && ok "https://events.adventist.club responde en esta Mac" || aviso "HTTPS todavía no responde; espera un minuto y mira el estado"
  paso "6/$total Evitando que la Mac se duerma y activando respaldos"
  start_awake
  start_mirror
  date '+%Y-%m-%d %H:%M:%S' > "$RUN_DIR/encendido"
  paso "7/$total Red"
  detect_net
  if [ "$NET_EN_ROUTER" = 1 ]; then
    ok "En la red del GL.iNet: esta Mac es $NET_IP"
    [ -n "$IP_FIJA" ] && [ "$IP_FIJA" != "$NET_IP" ] && aviso "La IP cambió ($IP_FIJA → $NET_IP): vuelve a «Configurar router»"
  else
    aviso "Conecta el GL.iNet (cable o Wi-Fi). El nodo ya está encendido; en cuanto aparezca la red, todo sigue solo."
  fi
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
  rm -f "$RUN_DIR/encendido"
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
  dc --profile sync run --rm -T -e PROD_DATABASE_URL sync "$sub" "${args[@]}" "$@" 2>&1 \
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
# Router GL.iNet (OpenWrt) por SSH + uci
sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

router_ssh() {  # router_ssh < script
  [ -n "${NODO_ROUTER_PASS:-}" ] || fallo "Falta la contraseña de administración del router."
  local askpass="$RUN_DIR/askpass.sh"
  # El ayudante sólo lee la contraseña del entorno (nunca la escribe en disco).
  printf '#!/bin/sh\nprintf "%%s\\n" "$NODO_ROUTER_PASS"\n' > "$askpass"; chmod 700 "$askpass"
  SSH_ASKPASS="$askpass" SSH_ASKPASS_REQUIRE=force DISPLAY="${DISPLAY:-:0}" \
  ssh -T -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$APP_SUPPORT/known_hosts_router" \
      -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive \
      -o NumberOfPasswordPrompts=1 -o ConnectTimeout=8 -o HostKeyAlgorithms=+ssh-rsa \
      "root@$ROUTER_IP" 'sh -s' < /dev/stdin
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
  local simular=0; [ "${1:-}" = "--simular" ] && simular=1
  router_target
  say "Router: $ROUTER_IP · esta Mac: $ROUTER_T_IFACE ($( [ "$(iface_kind "$ROUTER_T_IFACE")" = ethernet ] && echo Ethernet || echo Wi-Fi )) MAC $ROUTER_T_MAC"
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
    if [ "$mode" = plan ]; then router_read_script; else router_apply_script "$ROUTER_T_IP" "$ROUTER_T_MAC" | sed 's/KEY=.*/KEY=<oculta>/'; fi
    say "-----"
    ok "Simulación terminada"
    return 0
  fi
  router_alive || fallo "No veo el router en $ROUTER_IP. ¿Esta Mac está conectada al GL.iNet?"
  if [ "$mode" = plan ]; then
    say ""; say "Valores actuales en el router:"
    router_read_script | router_ssh 2>&1 | sed 's/^/  /'
    [ "${PIPESTATUS[1]}" = 0 ] || fallo "No pude entrar por SSH (¿contraseña de administración correcta?)"
    ok "Revisa los cambios y pulsa «Aplicar»"
  else
    router_apply_script "$ROUTER_T_IP" "$ROUTER_T_MAC" | router_ssh 2>&1 | sed 's/^/  /'
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
cmd_cert() {
  need_node_dir
  case "$1" in
    letsencrypt)
      [ -n "${CF_DNS_API_TOKEN:-}" ] || fallo "Falta el token de Cloudflare (Ajustes › Secretos)."
      [ -n "$LE_EMAIL" ] || fallo "Falta el correo para avisos de Let's Encrypt (Certificado › correo)."
      start_colima
      local hosts=(events.adventist.club admin.adventist.club api.adventist.club)
      say "Pidiendo el certificado (necesita internet; 1–3 min)…"
      LE_EMAIL="$LE_EMAIL" bash "$NODO_DIR/tls/certificado-letsencrypt.sh" "${@:2}" "${hosts[@]}" 2>&1 | grep -v '^ *$' | tail -25
      [ "${PIPESTATUS[0]}" = 0 ] || fallo "No se obtuvo el certificado (¿internet? ¿token con permiso DNS Edit?)"
      ;;
    local)
      detect_net
      local names=(events.adventist.club admin.adventist.club api.adventist.club localhost)
      [ -n "$IP_FIJA" ] && names+=("$IP_FIJA")
      [ -n "$NET_IP" ] && [ "$NET_IP" != "$IP_FIJA" ] && names+=("$NET_IP")
      bash "$NODO_DIR/tls/ca-local.sh" "${names[@]}" 2>&1 || fallo "No se pudo crear el certificado local"
      ;;
  esac
  docker inspect "$(container caddy)" >/dev/null 2>&1 && docker restart "$(container caddy)" >/dev/null && say "Caddy reiniciado con el certificado nuevo"
  ok "Certificado listo"
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
    https_get events.adventist.club /nodo-salud 127.0.0.1 && https_local=1
    [ $https_local = 1 ] && https_get events.adventist.club /auth/login 127.0.0.1 && web_ok=1
    [ $https_local = 1 ] && https_get api.adventist.club /api/v1/health 127.0.0.1 && api_ok=1
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
  if [ "$st_caddy" = running ] || [ "$st_caddy" = healthy ]; then
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
  local imgs=0; [ $docker_up = 1 ] && docker image inspect nodo-evento-api:local nodo-evento-web:local >/dev/null 2>&1 && imgs=1
  local encendido=""; [ -f "$RUN_DIR/encendido" ] && encendido="$(cat "$RUN_DIR/encendido")"
  local espejo=""; [ -f "$RUN_DIR/ultimo-espejo" ] && espejo="$(cat "$RUN_DIR/ultimo-espejo")"
  local ip_cambio=0; [ -n "$IP_FIJA" ] && [ "$NET_EN_ROUTER" = 1 ] && [ "$NET_IP" != "$IP_FIJA" ] && ip_cambio=1
  printf '{'
  printf '"ahora":%s,' "$(date +%s)"
  printf '"docker":%s,"colima":%s,"imagenes":%s,"encendido_desde":%s,' "$(jb $docker_up)" "$(jb $colima_up)" "$(jb $imgs)" "$(json_str "$encendido")"
  printf '"servicios":{"db":"%s","api":"%s","web":"%s","caddy":"%s","respaldo":"%s"},' "$st_db" "$st_api" "$st_web" "$st_caddy" "$st_resp"
  printf '"https_local":%s,"https_lan":%s,"web_ok":%s,"api_ok":%s,' "$(jb $https_local)" "$(jb $https_lan)" "$(jb $web_ok)" "$(jb $api_ok)"
  printf '"red":{"interfaz":%s,"tipo":%s,"ip":%s,"mac":%s,"en_red_router":%s,"router_responde":%s,"glinet":%s,"ip_fija":%s,"ip_cambio":%s},' \
    "$(json_str "$NET_IFACE")" "$(json_str "$NET_KIND")" "$(json_str "$NET_IP")" "$(json_str "$NET_MAC")" "$(jb "$NET_EN_ROUTER")" "$(jb $router_ok)" "$(jb $glinet)" "$(json_str "$IP_FIJA")" "$(jb $ip_cambio)"
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
  printf '"config":%s' "$(config_json)"
  printf '}\n'
}

cmd_registros() {
  need_node_dir
  local svc="${1:-}"
  if [ -n "$svc" ]; then docker logs --tail 150 "$(container "$svc")" 2>&1
  else for svc in db migrate api web caddy respaldo; do printf '===== %s =====\n' "$svc"; docker logs --tail 25 "$(container "$svc")" 2>&1; done; fi
  [ -f "$RUN_DIR/respaldo-usb.log" ] && { printf '===== copia a USB =====\n'; tail -10 "$RUN_DIR/respaldo-usb.log"; }
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
    --host-resolver-rules="MAP events.adventist.club $ip,MAP admin.adventist.club $ip,MAP api.adventist.club $ip" \
    "https://events.adventist.club/"
  ok "Abriendo el sitio local en $app (ventana aparte que apunta a esta Mac)"
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
  router-guion) router_target; router_apply_script "$ROUTER_T_IP" "$ROUTER_T_MAC" ;;  # sólo pruebas (pruebas/router-openwrt.sh)
  cert-letsencrypt) shift; cmd_cert letsencrypt "$@" ;;
  cert-local) cmd_cert local ;;
  autoarranque) shift; cmd_autoarranque "$@" ;;
  config) shift; case "${1:-}" in json) config_json; echo ;; set) config_set "$2" "${3-}"; ok "guardado: $2" ;; *) fallo "uso: config json|set CLAVE VALOR" ;; esac ;;
  registros) shift; cmd_registros "$@" ;;
  ver-sitio) cmd_ver_sitio ;;
  instalar) cmd_instalar ;;
  *) sed -n '2,26p' "$0"; exit 2 ;;
esac
