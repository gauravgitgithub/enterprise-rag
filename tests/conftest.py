import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from enterprise_rag.constants.db import APP_DB_ROLE, RELAY_DB_ROLE
from enterprise_rag.constants.retrieval import EMBEDDING_DIM
from enterprise_rag.db.models import Tenant
from enterprise_rag.db.session import make_engine, make_session_factory

APP_PASSWORD = "test-only"


@pytest.fixture(scope="session")
def pg() -> Iterator[PostgresContainer]:
    with PostgresContainer("pgvector/pgvector:pg18", driver="asyncpg") as container:
        yield container


@pytest.fixture(scope="session")
def migrated(pg: PostgresContainer) -> tuple[str, str]:
    admin_url = pg.get_connection_url()
    # Real env vars outrank .env, so tests never touch the dev database.
    os.environ["RAG_DATABASE_ADMIN_URL"] = admin_url
    command.upgrade(Config("alembic.ini"), "head")
    result = pg.get_wrapped_container().exec_run(
        [
            "psql",
            "-U",
            pg.username,
            "-d",
            pg.dbname,
            "-c",
            f"ALTER ROLE {APP_DB_ROLE} PASSWORD '{APP_PASSWORD}'; "
            f"ALTER ROLE {RELAY_DB_ROLE} PASSWORD '{APP_PASSWORD}'",
        ]
    )
    assert result.exit_code == 0, result.output
    app_url = admin_url.replace(f"{pg.username}:{pg.password}@", f"{APP_DB_ROLE}:{APP_PASSWORD}@")
    return admin_url, app_url


@pytest.fixture(scope="session")
async def relay_factory(
    migrated: tuple[str, str],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(migrated[1].replace(f"{APP_DB_ROLE}:", f"{RELAY_DB_ROLE}:"))
    yield make_session_factory(engine)
    await engine.dispose()


@pytest.fixture(scope="session")
async def admin_engine(migrated: tuple[str, str]) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(migrated[0])
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def app_factory(migrated: tuple[str, str]) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(migrated[1])
    yield make_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def make_tenant(admin_engine: AsyncEngine):  # type: ignore[no-untyped-def]
    async def _make() -> uuid.UUID:
        async with admin_engine.begin() as conn:
            result = await conn.execute(
                insert(Tenant).values(name=f"t-{uuid.uuid4()}").returning(Tenant.id)
            )
            return result.scalar_one()

    return _make


def onehot(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v
