"""
alembic/env.py
─────────────────────────────────────────────────────────────────────────────
Alembic migration environment.

* DATABASE_URL is read from app.config (which reads .env) so we never
  hardcode credentials here.
* run_migrations_offline() lets CI generate SQL scripts without a live DB.
* run_migrations_online() applies migrations to the hosted Postgres.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── load ORM metadata so Alembic knows what tables to diff ───────────────────
from app.db.models import Base  # noqa: E402 – must import after sys.path is set

# ── inject DATABASE_URL from .env / environment ───────────────────────────────
from app.config import settings  # noqa: E402

config = context.config

# Honour the logging config in alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the blank sqlalchemy.url in alembic.ini with the real value.
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


# ── offline migrations (generates a .sql file without connecting) ─────────────

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── online migrations (connects to the real DB) ───────────────────────────────

def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,   # migrations: one connection, no pooling
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
