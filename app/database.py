from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


settings = get_settings()

engine_options: dict[str, object] = {
    "pool_pre_ping": True,
    "future": True,
}
if settings.database_url.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def initialize_database(session_factory=SessionLocal) -> None:
    # Import model declarations before creating tables from SQLAlchemy metadata.
    from app import models  # noqa: F401

    with session_factory() as db:
        Base.metadata.create_all(bind=db.get_bind(), checkfirst=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
