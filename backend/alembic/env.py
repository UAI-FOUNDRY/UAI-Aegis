"""Alembic migrations (async). Every schema change is a new migration:

    alembic revision --autogenerate -m "add xyz"     # review the file it writes!
    alembic upgrade head
"""

from __future__ import annotations

import asyncio

from sqlalchemy.engine import Connection

from alembic import context
from app.config import Settings
from app.db import make_engine
from app.models import Base
from app.models.base import UTCDateTime

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("database_url") or Settings().database_url


def render_item(type_, obj, autogen_context):
    # Write our UTCDateTime decorator as a plain timezone-aware DateTime in migrations.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def _configure(connection: Connection | None = None, **kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_item=render_item,
        render_as_batch=True,  # lets ALTER TABLE work on SQLite in development
        compare_type=True,
        connection=connection,
        **kwargs,
    )


def run_offline() -> None:
    _configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = make_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
