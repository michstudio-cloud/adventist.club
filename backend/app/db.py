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

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def connect_args_for(url, *, raw: str | None = None) -> dict:
    """
    TLS is mandatory for any remote database (Neon). A database on the same
    machine (local development, CI, tests) usually has no TLS at all, and a
    unix-socket URL has no host.

    The one explicit exception is `sslmode=disable` in the ORIGINAL URL (`raw`;
    normalising drops libpq parameters): the event node (nodo-evento/) reaches its
    own Postgres as `db` on a private Docker network that has no TLS.
    """
    if url.host is None or url.host in LOCAL_HOSTS:
        return {}
    if raw is not None and make_url(raw).query.get("sslmode") == "disable":
        return {}
    return {"ssl": "require"}


database_url = normalize_database_url(settings.DATABASE_URL)

engine = create_async_engine(
    database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args=connect_args_for(database_url, raw=settings.DATABASE_URL),
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with SessionLocal() as session:
        yield session


def violated_constraint(exc: Exception) -> str | None:
    """
    Name of the constraint behind an IntegrityError (asyncpg reports it on the
    driver exception). Lets callers turn one specific, expected conflict into a
    409 while every other integrity error stays a visible failure.
    """
    driver_error = getattr(getattr(exc, "orig", None), "__cause__", None)
    return getattr(driver_error, "constraint_name", None)
