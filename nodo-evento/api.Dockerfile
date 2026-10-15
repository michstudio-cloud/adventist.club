# API del nodo local (contexto de build: <repo backend>/backend). La misma app que en Render,
# más lo que el nodo necesita para arrancar solo: psql (db_bootstrap.sh), migraciones, base y
# scripts (nodo_sync.py). Se construye ANTES del evento, con internet.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY migrations ./migrations
COPY db ./db
COPY scripts ./scripts
EXPOSE 8000
# --proxy-headers: Caddy pone X-Forwarded-For / -Proto.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
