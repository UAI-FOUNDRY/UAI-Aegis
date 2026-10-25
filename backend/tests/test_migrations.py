"""The Alembic migrations must build exactly the schema the models describe."""

import asyncio
import os

import pytest
from sqlalchemy import select, text

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db import make_engine
from app.models import AuditHead, Base


async def _reset_postgres(url: str) -> None:
    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_migrations_match_models(tmp_path, backend):
    if backend == "postgresql":
        url = os.environ.get("AEGIS_TEST_DATABASE_URL")
        if not url:
            pytest.skip("set AEGIS_TEST_DATABASE_URL to also check migrations on PostgreSQL")
        asyncio.run(_reset_postgres(url))
    else:
        url = f"sqlite+aiosqlite:///{tmp_path}/migrated.db"
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")

    async def inspect():
        engine = make_engine(url)
        async with engine.connect() as conn:
            diff = await conn.run_sync(lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata))
            head = (await conn.execute(select(AuditHead.seq, AuditHead.hash))).one()
        await engine.dispose()
        return diff, head

    diff, head = asyncio.run(inspect())
    assert diff == [], diff
    assert tuple(head) == (0, "0" * 64)
    command.downgrade(cfg, "base")
