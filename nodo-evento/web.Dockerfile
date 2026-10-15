# Web (Next) del nodo local (contexto de build: el repo del frontend). Build de producción; el
# servidor de Next habla con la API del nodo por la red interna (API_INTERNAL_URL, en runtime).
# Las fuentes (next/font) se descargan AQUÍ, al construir: en el evento no hace falta internet.
FROM node:22-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Mismo commit que producción => mismo BUILD_ID y la caché del service worker sigue coherente.
ARG BUILD_ID=nodo-local
# Llamadas directas del navegador (catálogo, certificados): el host de producción, que el router
# del evento también resuelve al nodo. Ninguna pantalla de eventos las usa.
ARG NEXT_PUBLIC_CERTIFICATES_API_URL=https://api.adventist.club
ENV BUILD_ID=$BUILD_ID NEXT_PUBLIC_CERTIFICATES_API_URL=$NEXT_PUBLIC_CERTIFICATES_API_URL
# Sin `npm prune --omit=dev`: `next start` transpila next.config.ts con TypeScript (devDependency)
# y, si falta, intenta instalarlo de internet al arrancar (lo descubrió la red sin salida).
RUN npm run build

FROM node:22-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
# Todo el árbol: next.config.ts importa lib/ al arrancar.
COPY --from=build /app ./
EXPOSE 3000
CMD ["node_modules/.bin/next", "start", "-p", "3000", "-H", "0.0.0.0"]
