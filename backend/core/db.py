from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import get_settings

engine = create_engine(get_settings().database_url, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Declarative base that every ORM model inherits from; SQLAlchemy collects table metadata here."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a DB session for one request and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
