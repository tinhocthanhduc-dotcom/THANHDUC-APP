from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_factory = None


def engine():
    global _engine, _factory
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
        _factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Dùng trong test khi đổi DATABASE_URL."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = _factory = None


def get_session() -> Iterator[Session]:
    engine()
    with _factory() as session:
        yield session
