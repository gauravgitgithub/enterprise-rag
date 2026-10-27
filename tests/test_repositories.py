import hashlib
import uuid

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_rag.constants.db import GROUPS_SETTING, TENANT_SETTING
from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Chunk, Document
from enterprise_rag.repositories.uow import UnitOfWork
from tests.conftest import onehot

EVERYONE = frozenset({"everyone"})


def _doc(acl: list[str], title: str = "Handbook") -> Document:
    return Document(
        title=title,
        source_uri=f"s3://test/{uuid.uuid4()}",
        content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        acl_groups=acl,
    )


def _chunk(ordinal: int, content: str, embedding: list[float]) -> Chunk:
    return Chunk(
        ordinal=ordinal, content=content, char_start=0, char_end=len(content), embedding=embedding
    )


async def _seed(
    factory: async_sessionmaker[AsyncSession], ctx: RequestContext, acl: list[str]
) -> uuid.UUID:
    async with UnitOfWork(factory, ctx) as uow:
        doc, _ = await uow.documents.register(_doc(acl))
        await uow.chunks.add_many(
            doc,
            [
                _chunk(0, "Refund policy: customers may return items within 30 days.", onehot(0)),
                _chunk(1, "Section 4.2(b): SKU-99812 is excluded from warranty.", onehot(5)),
                _chunk(2, "Office opening hours are 9 to 5.", onehot(9)),
            ],
        )
        return doc.id


async def test_hybrid_search_fuses_dense_and_keyword_hits(app_factory, make_tenant):
    ctx = RequestContext(await make_tenant(), EVERYONE)
    await _seed(app_factory, ctx, ["everyone"])

    async with UnitOfWork(app_factory, ctx) as uow:
        hits = await uow.chunks.hybrid_search("SKU-99812", onehot(0), top_n=3)

    by_ordinal = {h.chunk.ordinal: h for h in hits}
    # Exact-term match wins: it ranks in both lists, the semantic hit only in dense.
    assert hits[0].chunk.ordinal == 1
    assert by_ordinal[1].sparse_rank == 1
    assert by_ordinal[0].dense_rank == 1
    assert by_ordinal[0].sparse_rank is None


async def test_other_tenant_sees_nothing(app_factory, make_tenant):
    owner = RequestContext(await make_tenant(), EVERYONE)
    intruder = RequestContext(await make_tenant(), EVERYONE)
    doc_id = await _seed(app_factory, owner, ["everyone"])

    async with UnitOfWork(app_factory, intruder) as uow:
        assert await uow.chunks.hybrid_search("SKU-99812", onehot(0)) == []
        assert await uow.documents.get(doc_id) is None
        assert await uow.documents.delete(doc_id) is False

    async with UnitOfWork(app_factory, owner) as uow:
        assert await uow.documents.get(doc_id) is not None


async def test_acl_hides_chunks_from_users_outside_the_group(app_factory, make_tenant):
    tenant = await make_tenant()
    await _seed(app_factory, RequestContext(tenant, frozenset({"exec"})), ["exec"])

    async with UnitOfWork(app_factory, RequestContext(tenant, EVERYONE)) as uow:
        assert await uow.chunks.hybrid_search("SKU-99812", onehot(0)) == []

    async with UnitOfWork(
        app_factory, RequestContext(tenant, frozenset({"exec", "everyone"}))
    ) as uow:
        assert len(await uow.chunks.hybrid_search("SKU-99812", onehot(0))) == 3


async def test_session_without_tenant_context_reads_nothing(app_factory, make_tenant):
    await _seed(app_factory, RequestContext(await make_tenant(), EVERYONE), ["everyone"])

    # Bypasses the UnitOfWork on purpose: RLS must hold even if application code forgets.
    async with app_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Chunk)) == 0
        assert await session.scalar(select(func.count()).select_from(Document)) == 0


async def test_rls_rejects_writes_into_another_tenant(app_factory, make_tenant):
    mine, theirs = await make_tenant(), await make_tenant()

    async with app_factory() as session, session.begin():
        await session.execute(
            select(
                func.set_config(TENANT_SETTING, str(mine), True),
                func.set_config(GROUPS_SETTING, "everyone", True),
            )
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.execute(
                insert(Document).values(
                    tenant_id=theirs,
                    title="x",
                    source_uri="s3://x",
                    content_hash="0" * 64,
                    acl_groups=["everyone"],
                    version=1,
                    status="pending",
                )
            )


async def test_pooled_connection_does_not_leak_tenant_context(app_factory, make_tenant):
    ctx = RequestContext(await make_tenant(), EVERYONE)
    await _seed(app_factory, ctx, ["everyone"])
    async with UnitOfWork(app_factory, ctx) as uow:
        assert await uow.chunks.hybrid_search("SKU-99812", onehot(0))

    async with app_factory() as session:
        assert await session.scalar(select(func.current_setting(TENANT_SETTING, True))) in (
            None,
            "",
        )


@pytest.mark.parametrize("groups", [frozenset(), frozenset({"a,b"}), frozenset({"x' OR 1=1"})])
def test_request_context_rejects_bad_groups(groups):
    with pytest.raises(ValueError):
        RequestContext(uuid.uuid4(), groups)


async def test_rls_hides_acl_restricted_chunks_even_without_repository_filters(
    app_factory, make_tenant
):
    tenant = await make_tenant()
    await _seed(app_factory, RequestContext(tenant, frozenset({"exec"})), ["exec"])

    # Tenant is right, groups are wrong, and no repository predicate is applied.
    async with app_factory() as session, session.begin():
        await session.execute(
            select(
                func.set_config(TENANT_SETTING, str(tenant), True),
                func.set_config(GROUPS_SETTING, "everyone", True),
            )
        )
        assert await session.scalar(select(func.count()).select_from(Chunk)) == 0
