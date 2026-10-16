from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401  (nạp toàn bộ bảng)
from app.config import get_settings
from app.db import Base

target_metadata = Base.metadata


def run() -> None:
    url = context.config.attributes.get("database_url") or get_settings().database_url
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run()
