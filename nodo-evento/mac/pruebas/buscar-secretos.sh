#!/bin/bash
# Busca secretos antes de que salgan de la Mac en un commit o un push (docs/EGRESO.md §6).
# Nunca imprime el valor: sólo «archivo:línea: tipo».
#
#   nodo-evento/mac/pruebas/buscar-secretos.sh             revisa el árbol: pruebas/muestras/** y todo
#                                                         *.txt/*.json/*.log/*.leases bajo nodo-evento/
#                                                         (criterio estricto) + llaves/tokens en todo el repo
#   nodo-evento/mac/pruebas/buscar-secretos.sh --staged    lo que está en el índice (gancho pre-commit)
#   nodo-evento/mac/pruebas/buscar-secretos.sh --push      lo que se va a subir (gancho pre-push; lee stdin de git)
#   nodo-evento/mac/pruebas/buscar-secretos.sh --instalar  instala los ganchos pre-commit y pre-push en este clon
#
# Criterio estricto (muestras y textos del nodo): llaves y tokens (AIza…, AQ.…, ya29., ghp_/gho_/
# github_pat_, sk-…, xox?-, AKIA…), Bearer/Basic, cabeceras X-…-Key/Token, KEY=valor, --token/--password,
# credenciales en URLs, JWT, PEM privados, base64 largo junto a key/token/secret, contraseñas del Wi-Fi,
# IPv6 con la MAC (EUI-64), rutas /Users/<nombre real> y el nombre de esta Mac o de su usuario.
# Resto del repo: sólo llaves/tokens con forma inconfundible (el código y las pruebas usan URLs y
# palabras como «Bearer» legítimamente). Si gitleaks está instalado, también corre (sin red).
set -uo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
DEPURAR="$AQUI/../depurar.pl"
RAIZ="$(git -C "$AQUI" rev-parse --show-toplevel)"
cd "$RAIZ" || exit 2
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

estricto() {  # ¿esta ruta (relativa al repo) se revisa con el criterio estricto?
  case "$1" in
    nodo-evento/mac/pruebas/muestras/*) return 0 ;;
    nodo-evento/mac/pruebas/depurador/*) return 1 ;;   # casos con marcadores {{…}}, sin secretos
    nodo-evento/*.txt|nodo-evento/*.json|nodo-evento/*.log|nodo-evento/*.leases) return 0 ;;
  esac
  return 1
}

fallos=0
revisar() {  # revisar MODO RUTA_REAL RUTA_PARA_MOSTRAR
  local salida
  if [ "$1" = estricto ]; then salida="$(perl "$DEPURAR" --buscar "$2")"
  else salida="$(perl "$DEPURAR" --buscar --solo-tokens "$2")"; fi
  if [ -n "$salida" ]; then
    printf '%s\n' "$salida" | sed "s|^$2:|$3:|"
    fallos=1
  fi
}

copia_indice() {  # copia_indice RUTA → archivo temporal con el contenido del índice
  local dest="$TMP/indice/$1"
  mkdir -p "$(dirname "$dest")"
  git show ":$1" > "$dest" 2>/dev/null
  printf '%s' "$dest"
}

modo="${1:-arbol}"
case "$modo" in
  --instalar)
    hooks="$(git rev-parse --git-path hooks)"
    mkdir -p "$hooks"
    for h in pre-commit pre-push; do
      if [ -e "$hooks/$h" ] && ! grep -q 'buscar-secretos.sh' "$hooks/$h"; then
        echo "Ya existe $hooks/$h (no es nuestro): añade a mano  nodo-evento/mac/pruebas/hooks/$h" >&2; continue
      fi
      install -m 755 "$AQUI/hooks/$h" "$hooks/$h"
      echo "instalado $hooks/$h"
    done
    exit 0 ;;
  --staged)
    while IFS= read -r -d '' f; do
      archivo="$(copia_indice "$f")"
      if estricto "$f"; then revisar estricto "$archivo" "$f"; else revisar tokens "$archivo" "$f"; fi
    done < <(git diff --cached --name-only --diff-filter=ACMR -z)
    ;;
  --push)
    # stdin de git: <ref local> <sha local> <ref remota> <sha remota>
    cero=0000000000000000000000000000000000000000
    while read -r _ local _ remota; do
      [ "$local" = "$cero" ] && continue
      if [ "$remota" = "$cero" ]; then rango="$local --not --remotes"; else rango="$remota..$local"; fi
      # Cada archivo tocado, tal como queda en cada commit que se sube.
      # shellcheck disable=SC2086
      for c in $(git rev-list $rango); do
        while IFS= read -r -d '' f; do
          dest="$TMP/push/$c/$f"; mkdir -p "$(dirname "$dest")"
          git show "$c:$f" > "$dest" 2>/dev/null || continue
          if estricto "$f"; then revisar estricto "$dest" "$c:$f"; else revisar tokens "$dest" "$c:$f"; fi
        done < <(git diff-tree --no-commit-id --name-only --diff-filter=ACMR -r -z "$c")
      done
    done
    ;;
  arbol|--arbol)
    while IFS= read -r -d '' f; do
      [ -f "$f" ] || continue
      if estricto "$f"; then revisar estricto "$f" "$f"; else revisar tokens "$f" "$f"; fi
    done < <(git ls-files -z --cached --others --exclude-standard)
    ;;
  *) echo "uso: buscar-secretos.sh [--staged | --push | --instalar]" >&2; exit 2 ;;
esac

if command -v gitleaks >/dev/null 2>&1 && [ "$modo" != --push ]; then
  if [ "$modo" = --staged ]; then gitleaks protect --staged --no-banner --redact >/dev/null 2>&1 || { echo "gitleaks encontró algo (gitleaks protect --staged --redact)"; fallos=1; }
  else gitleaks detect --no-git --source nodo-evento --no-banner --redact >/dev/null 2>&1 || { echo "gitleaks encontró algo en nodo-evento/ (gitleaks detect --no-git --source nodo-evento --redact)"; fallos=1; }; fi
fi

if [ $fallos = 1 ]; then
  cat >&2 <<'EOF'

✗ Hay algo con forma de secreto (arriba: archivo, línea y tipo; el valor no se muestra).
  Muestras: vuelve a capturarlas con  nodo-evento/mac/pruebas/capturar-muestras.sh GRUPO
  o depúralas con  perl nodo-evento/mac/depurar.pl [--procesos] archivo > archivo.nuevo
  Si una llave real llegó a un commit: rótala (cámbiala en su servicio) aunque luego se borre.
EOF
  exit 1
fi
echo "Sin secretos a la vista."
