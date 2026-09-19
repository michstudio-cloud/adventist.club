from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import settings

def normalize_database_url(raw: str):
    """
    Neon provides a standard postgresql:// URL. This API is async, so force
    SQLAlchemy's asyncpg dialect and pass TLS through connect_args instead of
    psycopg2-style URL parameters.
    """
    url = make_url(raw)
    if url.drivername in {"postgresql", "postgres"}:
        url = url.set(drivername="postgresql+asyncpg")
    elif url.drivername.startswith("postgresql+psycopg"):
        url = url.set(drivername="postgresql+asyncpg")

    # Neon connection strings can include libpq-only parameters that asyncpg
    # does not understand directly.
    query = dict(url.query)
    query.pop("sslmode", None)
    query.pop("channel_binding", None)
    url = url.set(query=query)
    return url

database_url = normalize_database_url(settings.DATABASE_URL)

engine = create_async_engine(
    database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"ssl": "require"},
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with SessionLocal() as session:
        yield session
