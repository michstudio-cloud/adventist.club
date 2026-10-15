#!/usr/bin/env bash
# Descarga (una vez) Sparkle, el marco de actualizaciones de la app (MIT, sparkle-project.org), en
# una versión FIJA y comprobada por sha256. Imprime la carpeta donde quedó. No se guarda en el repo.
#   ./nodo-evento/mac/obtener-sparkle.sh            → ~/Library/Caches/nodo-camporee-build/Sparkle-<versión>
set -euo pipefail
VER="2.9.6"
SHA="52bf9e88cdd972fc0c81501377a880e90d47031bd8ca5462488f843e2609e192"
URL="https://github.com/sparkle-project/Sparkle/releases/download/$VER/Sparkle-$VER.tar.xz"
CACHE="${NODO_BUILD_CACHE:-$HOME/Library/Caches/nodo-camporee-build}"
DEST="$CACHE/Sparkle-$VER"
if [ -d "$DEST/Sparkle.framework" ] && [ -x "$DEST/bin/generate_appcast" ]; then echo "$DEST"; exit 0; fi
mkdir -p "$CACHE/dl"
F="$CACHE/dl/Sparkle-$VER.tar.xz"
if [ ! -s "$F" ] || [ "$(shasum -a 256 "$F" | cut -d' ' -f1)" != "$SHA" ]; then
  curl -fsSL --retry 3 -o "$F.parcial" "$URL" >&2
  [ "$(shasum -a 256 "$F.parcial" | cut -d' ' -f1)" = "$SHA" ] || { rm -f "$F.parcial"; echo "sha256 de Sparkle no coincide" >&2; exit 1; }
  mv "$F.parcial" "$F"
fi
rm -rf "$DEST.tmp" && mkdir -p "$DEST.tmp" && tar -xf "$F" -C "$DEST.tmp" && rm -rf "$DEST" && mv "$DEST.tmp" "$DEST"
echo "$DEST"
