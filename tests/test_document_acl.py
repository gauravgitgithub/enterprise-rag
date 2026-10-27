import hashlib
import uuid

import pytest
from sqlalchemy import delete, func, select, update

from enterprise_rag.constants.db import GROUPS_SETTING, TENANT_SETTING
from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Chunk, Document
from enterprise_rag.repositories.documents import DocumentNotVisibleError
from enterprise_rag.repositories.uow import UnitOfWork
from tests.conftest import onehot

EXEC = frozenset({"exec"})
STAFF = frozenset({"everyone"})


def _exec_doc() -> Document:
    return Document(
        title="Q3 restructuring plan",
        source_uri=f"s3://t/{uuid.uuid4()}",
        content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        acl_groups=["exec"],
    )


async def _seed_exec_doc(app_factory, tenant) -> Document:
    async with UnitOfWork(app_factory, RequestContext(tenant, EXEC)) as uow:
        doc, _ = await uow.documents.register(_exec_doc())
        await uow.chunks.add_many(
            doc,
            [Chunk(ordinal=0, content="secret", char_start=0, char_end=6, embedding=onehot(1))],
        )
        return doc


# Break caught: documents table filtered by tenant only, leaking restricted titles/URIs.
async def test_restricted_document_metadata_is_invisible_outside_its_groups(
    app_factory, make_tenant
):
    tenant = await make_tenant()
    doc = await _seed_exec_doc(app_factory, tenant)

    async with UnitOfWork(app_factory, RequestContext(tenant, STAFF)) as uow:
        assert await uow.documents.get(doc.id) is None
        assert await uow.documents.get_by_hash(doc.content_hash) is None


# Break caught: DELETE allowed on rows the caller cannot read.
async def test_user_outside_groups_cannot_delete_restricted_document(app_factory, make_tenant):
    tenant = await make_tenant()
    doc = await _seed_exec_doc(app_factory, tenant)

    async with UnitOfWork(app_factory, RequestContext(tenant, STAFF)) as uow:
        assert await uow.documents.delete(doc.id) is False
        assert await uow.chunks.delete_for_document(doc) == 0

    async with UnitOfWork(app_factory, RequestContext(tenant, EXEC)) as uow:
        assert await uow.documents.get(doc.id) is not None
        assert await uow._session.scalar(select(func.count()).select_from(Chunk)) == 1


# Break caught: register() crashing or returning someone else's restricted document.
async def test_registering_content_of_an_invisible_document_is_refused(app_factory, make_tenant):
    tenant = await make_tenant()
    doc = await _seed_exec_doc(app_factory, tenant)
    duplicate = _exec_doc()
    duplicate.content_hash = doc.content_hash
    duplicate.acl_groups = ["everyone"]

    with pytest.raises(DocumentNotVisibleError):
        async with UnitOfWork(app_factory, RequestContext(tenant, STAFF)) as uow:
            await uow.documents.register(duplicate)


# Break caught: missing UPDATE/DELETE policies. A bare statement reads no columns, so Postgres
# skips SELECT policies and only the write policies stand between staff and exec rows.
@pytest.mark.parametrize(("model", "column"), [(Chunk, Chunk.content), (Document, Document.title)])
async def test_bare_update_and_delete_cannot_touch_rows_outside_groups(
    app_factory, make_tenant, model, column
):
    tenant = await make_tenant()
    await _seed_exec_doc(app_factory, tenant)

    async with app_factory() as session, session.begin():
        await session.execute(
            select(
                func.set_config(TENANT_SETTING, str(tenant), True),
                func.set_config(GROUPS_SETTING, "everyone", True),
            )
        )
        await session.execute(update(model).values({column: "overwritten"}))
        await session.execute(delete(model))

    async with UnitOfWork(app_factory, RequestContext(tenant, EXEC)) as uow:
        assert list(await uow._session.scalars(select(column))) == [
            "secret" if model is Chunk else "Q3 restructuring plan"
        ]
