# Adventist Club API

Backend global para varios ministerios y aplicaciones.

Stack: FastAPI + PostgreSQL/Neon + Render/Docker.

En Render sólo debes configurar el secreto `DATABASE_URL` con la conexión de Neon. No la guardes en GitHub.

Endpoints iniciales:
- GET /api/v1/health
- GET /api/v1/ministries
- GET /api/v1/applications
- GET /api/v1/honors?ministry=pathfinders
- POST /api/v1/certificates/prototype-batch
- GET /api/v1/certificates/verify/{certificate_no}
- POST /api/v1/printing/pdf
