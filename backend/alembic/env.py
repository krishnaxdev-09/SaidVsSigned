"""Alembic env: sync psycopg driver derived from DATABASE_URL (the app itself uses asyncpg)."""

import os

from sqlalchemy import create_engine, pool

from alembic import context

url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://saidsigned:saidsigned@localhost:5432/saidsigned")
url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
