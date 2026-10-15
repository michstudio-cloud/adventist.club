#!/usr/bin/env bash
# Operación del nodo local del evento (README.md). Ejecutar desde cualquier carpeta.
#
#   ./nodo.sh init                 crea .env con los secretos propios del nodo y pide EVENT_PASS_SECRET
#   ./nodo.sh construir            construye las imágenes (CON internet, antes del evento)
#   ./nodo.sh arrancar             levanta todo y espera a que esté sano
#   ./nodo.sh apagar               detiene todo (los datos se conservan)
#   ./nodo.sh estado               servicios, salud por HTTPS y último respaldo
#   ./nodo.sh reiniciar [servicio] reinicia uno (api, web, caddy, db, respaldo) o todos
#   ./nodo.sh logs [servicio]      últimos registros (Ctrl+C para salir)
#   ./nodo.sh export  --event <id> --actor-email <correo> [--node-label …] [--dry-run]
#   ./nodo.sh import  --event <id> --actor-email <correo> [--dry-run] [--prefer node|prod]
#   ./nodo.sh status  --event <id>
#                                  export/import/status piden PROD_DATABASE_URL sin mostrarla
#   ./nodo.sh respaldar            un respaldo ahora (además del automático)
#   ./nodo.sh restaurar <archivo.dump>   vuelve la base del nodo a ese respaldo
set -euo pipefail
cd "$(dirname "$0")"
export NODO_DIR_ABS="$PWD"
DC=(docker compose --env-file .env)

need_env() {
  [ -f .env ] || { echo "falta .env: corre ./nodo.sh init" >&2; exit 1; }
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
}

gen() { openssl rand -hex 32; }

set_var() {  # set_var NAME VALUE  (en .env, sin mostrar el valor)
  local name="$1" value="$2" tmp
  tmp="$(mktemp)"
  grep -v "^${name}=" .env > "$tmp" || true
  printf '%s=%s\n' "$name" "$value" >> "$tmp"
  cat "$tmp" > .env && rm -f "$tmp"
}

cmd_init() {
  umask 077
  [ -f .env ] || cp .env.example .env
  chmod 600 .env
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
  [ -n "${POSTGRES_PASSWORD:-}" ] || { set_var POSTGRES_PASSWORD "$(gen)"; echo "POSTGRES_PASSWORD generada"; }
  [ -n "${JWT_SECRET:-}" ] || { set_var JWT_SECRET "$(gen)"; echo "JWT_SECRET propio del nodo generado"; }
  [ -n "${PROXY_SHARED_SECRET:-}" ] || { set_var PROXY_SHARED_SECRET "$(gen)"; echo "PROXY_SHARED_SECRET propio del nodo generado"; }
  if [ -z "${EVENT_PASS_SECRET:-}" ]; then
    if [ -t 0 ]; then
      printf 'EVENT_PASS_SECRET de producción (no se muestra; Enter para dejarlo para después): '
      read -rs secret; echo
      [ -z "$secret" ] || { set_var EVENT_PASS_SECRET "$secret"; echo "EVENT_PASS_SECRET guardado en .env (600)"; }
    else
      echo "AVISO: falta EVENT_PASS_SECRET en .env (igual al de producción): ver README «Secretos»"
    fi
  fi
  mkdir -p datos/postgres datos/media datos/exports datos/caddy respaldos tls/actual
  echo "listo. Siguiente: certificado (tls/certificado-letsencrypt.sh o tls/ca-local.sh) y ./nodo.sh construir"
}

cmd_up() {
  need_env
  [ -s tls/actual/fullchain.pem ] && [ -s tls/actual/privkey.pem ] || {
    echo "falta el certificado en tls/actual/: corre tls/certificado-letsencrypt.sh (plan A) o tls/ca-local.sh (plan B)" >&2
    exit 1; }
  mkdir -p "${DATOS_DIR:-./datos}/media" "${DATOS_DIR:-./datos}/exports" "${RESPALDO_DIR:-./respaldos}"
  "${DC[@]}" up -d --wait db migrate api web caddy respaldo 2>&1 | grep -v "^ *$" || true
  "${DC[@]}" ps --format 'table {{.Service}}\t{{.Status}}'
  cmd_health
}

cmd_health() {
  local ip
  ip="${NODO_IP:-127.0.0.1}"; [ -n "$ip" ] || ip=127.0.0.1
  local ca=() ; [ -s tls/actual/ca-local.pem ] && ca=(--cacert tls/actual/ca-local.pem)
  if curl -fsS --max-time 5 "${ca[@]}" --resolve "events.adventist.club:443:${ip}" \
       https://events.adventist.club/nodo-salud >/dev/null 2>&1 \
     && curl -fsS --max-time 5 "${ca[@]}" --resolve "events.adventist.club:443:${ip}" \
       https://events.adventist.club/auth/login >/dev/null 2>&1 \
     && curl -fsS --max-time 5 "${ca[@]}" --resolve "api.adventist.club:443:${ip}" \
       https://api.adventist.club/api/v1/health >/dev/null 2>&1; then
    echo "HTTPS: https://events.adventist.club responde en ${ip} (web + API)"
  else
    echo "HTTPS: NO responde en ${ip} — revisa ./nodo.sh logs caddy / web / api" >&2
    return 1
  fi
}

cmd_status() {
  need_env
  "${DC[@]}" ps --format 'table {{.Service}}\t{{.Status}}'
  cmd_health || true
  local dir="${RESPALDO_DIR:-./respaldos}"
  if [ -f "$dir/ultimo.dump" ]; then
    echo "último respaldo: $(ls -l "$dir"/nodo-*.dump 2>/dev/null | tail -1 | awk '{print $6, $7, $8, $9}')"
  else
    echo "todavía no hay respaldos en $dir"
  fi
}

ask_prod_url() {
  if [ -z "${PROD_DATABASE_URL:-}" ]; then
    printf 'PROD_DATABASE_URL (cadena de conexión de producción; no se muestra): '
    read -rs PROD_DATABASE_URL; echo
    export PROD_DATABASE_URL
  fi
}

cmd_sync() {
  need_env
  case "$1" in export|import|status) ;; *) echo "uso: ./nodo.sh export|import|status …" >&2; exit 2 ;; esac
  ask_prod_url
  # -e PROD_DATABASE_URL sin valor: Docker lo toma del entorno, no queda en la línea de órdenes.
  "${DC[@]}" --profile sync run --rm -e PROD_DATABASE_URL sync "$@"
}

cmd_backup_now() {
  need_env
  "${DC[@]}" exec -T respaldo sh -c \
    'f=/respaldos/nodo-$(date +%Y%m%d-%H%M%S)-manual.dump; pg_dump -Fc -Z 6 -f "$f.tmp" && mv "$f.tmp" "$f" && cp "$f" /respaldos/ultimo.dump && echo "ok $f"'
}

cmd_restore() {
  need_env
  local file="${1:?uso: ./nodo.sh restaurar <archivo.dump> (de RESPALDO_DIR)}"
  local dir="${RESPALDO_DIR:-./respaldos}" name
  name="$(basename "$file")"
  [ -f "$dir/$name" ] || { echo "no existe $dir/$name" >&2; exit 1; }
  printf 'Esto REEMPLAZA la base del nodo por %s. Escribe RESTAURAR para seguir: ' "$name"
  read -r ok; [ "$ok" = RESTAURAR ] || { echo "cancelado"; exit 1; }
  "${DC[@]}" stop api web
  cmd_backup_now || true   # por si acaso: lo de ahora también queda guardado
  "${DC[@]}" exec -T respaldo pg_restore --clean --if-exists --no-owner -d nodo "/respaldos/$name"
  "${DC[@]}" start api web
  echo "restaurado desde $name"
}

case "${1:-}" in
  init) cmd_init ;;
  construir|build) need_env; "${DC[@]}" build ;;
  arrancar|up) cmd_up ;;
  apagar|down) need_env; "${DC[@]}" down ;;
  estado) cmd_status ;;
  reiniciar|restart) need_env; shift; "${DC[@]}" restart "$@" ;;
  logs) need_env; shift; "${DC[@]}" logs --tail 200 -f "$@" ;;
  export|import|status) cmd_sync "$@" ;;
  respaldar) cmd_backup_now ;;
  restaurar) shift; cmd_restore "$@" ;;
  *) sed -n '2,17p' "$0"; exit 2 ;;
esac
