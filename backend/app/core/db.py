from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=10,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()

class CloudDatabaseNotConfigured(RuntimeError):
    """Raised when CLOUD_DATABASE_URL is unset but the cloud store is used."""


def cloud_database_url() -> str | None:
    """Normalised cloud URL, or None when not configured.

    Accepts postgresql:// or postgresql+psycopg:// — the driver installed
    here is psycopg 3, so a bare postgresql:// is upgraded automatically.
    """
    url = (settings.cloud_database_url or "").strip()
    if not url:
        return None
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    raise CloudDatabaseNotConfigured(
        "CLOUD_DATABASE_URL must start with postgresql:// "
        f"(got {url.split('://')[0]}://…)"
    )


_cloud_session_factory: sessionmaker | None = None


def get_cloud_engine():
    """Engine for the cloud store — created lazily on first use."""
    url = cloud_database_url()
    if url is None:
        raise CloudDatabaseNotConfigured(
            "CLOUD_DATABASE_URL is not set — cloud analytics store unavailable"
        )
    return create_engine(url, pool_pre_ping=True, future=True)


def get_cloud_session_factory() -> sessionmaker:
    """Cached sessionmaker for the cloud store."""
    global _cloud_session_factory
    if _cloud_session_factory is None:
        _cloud_session_factory = sessionmaker(
            bind=get_cloud_engine(),
            autoflush=False,
            autocommit=False,
        )
    return _cloud_session_factory


def get_cloud_db():
    """FastAPI dependency yielding a cloud-store session."""
    db = get_cloud_session_factory()()
    try:
        yield db
    finally:
        db.close()