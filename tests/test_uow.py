import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_rag.context import RequestContext
from enterprise_rag.db.session import make_engine
from enterprise_rag.repositories.uow import UnitOfWork


class _FailsAfterConnecting(AsyncSession):
    """Holds a real pooled connection, then fails like a server-side error would."""

    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        await super().execute(select(1))
        raise RuntimeError("server went away")


# Break caught: __aenter__ failing after checkout never closes the session, leaking the
# connection until GC. A burst of these exhausts the pool and every request then hangs.
async def test_failed_setup_returns_the_connection_to_the_pool(migrated, make_tenant):
    engine = make_engine(migrated[1])
    factory = async_sessionmaker(engine, class_=_FailsAfterConnecting)
    ctx = RequestContext(await make_tenant(), frozenset({"everyone"}))

    with pytest.raises(RuntimeError, match="server went away"):
        async with UnitOfWork(factory, ctx):
            pass

    assert engine.pool.checkedout() == 0
    await engine.dispose()
