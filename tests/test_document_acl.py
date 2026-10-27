import hashlib
import uuid

import pytest
from sqlalchemy import delete, func, select, update

from enterprise_rag.constants.db import GROUPS_SETTING, TENANT_SETTING
from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Chunk, Document
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


# Break caught: DELETE allowed on rows the caller cannot read.
async def test_user_outside_groups_cannot_delete_restricted_document(
    app_factory, admin_engine, make_tenant
):
    tenant = await make_tenant()
    doc = await _seed_exec_doc(app_factory, tenant)

    async with UnitOfWork(app_factory, RequestContext(tenant, STAFF)) as uow:
        assert await uow.documents.delete(doc.id) is False
        assert await uow.chunks.delete_for_document(doc) == 0

    # Superuser view: ground truth, not filtered by RLS.
    async with admin_engine.connect() as conn:
        assert (
            await conn.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == doc.id)
            )
            == 1
        )
        assert await conn.scalar(select(func.count()).where(Document.id == doc.id)) == 1


# Break caught: dedupe key without the ACL. A hidden duplicate would either refuse the upload
# (revealing it exists) or hand back a document with someone else's ACL.
async def test_same_content_with_a_different_acl_is_a_separate_document(app_factory, make_tenant):
    tenant = await make_tenant()
    doc = await _seed_exec_doc(app_factory, tenant)
    public_copy = _exec_doc()
    public_copy.content_hash = doc.content_hash
    public_copy.acl_groups = ["everyone"]

    async with UnitOfWork(app_factory, RequestContext(tenant, STAFF)) as uow:
        copy, created = await uow.documents.register(public_copy)

    assert created is True
    assert copy.id != doc.id
    assert copy.acl_groups == ["everyone"]


# Break caught: ACL compared in caller order, so ["b", "a"] and ["a", "b"] become two documents.
async def test_acl_order_does_not_create_a_duplicate(app_factory, make_tenant):
    ctx = RequestContext(await make_tenant(), frozenset({"a", "b"}))
    first, second = _exec_doc(), _exec_doc()
    second.content_hash = first.content_hash
    first.acl_groups, second.acl_groups = ["b", "a"], ["a", "b"]

    async with UnitOfWork(app_factory, ctx) as uow:
        doc1, _ = await uow.documents.register(first)
    async with UnitOfWork(app_factory, ctx) as uow:
        doc2, created = await uow.documents.register(second)

    assert (created, doc2.id) == (False, doc1.id)


# Break caught: missing UPDATE/DELETE policies. A bare statement reads no columns, so Postgres
# skips SELECT policies and only the write policies stand between staff and exec rows.
@pytest.mark.parametrize(("model", "column"), [(Chunk, Chunk.content), (Document, Document.title)])
async def test_bare_update_and_delete_cannot_touch_rows_outside_groups(
    app_factory, admin_engine, make_tenant, model, column
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

    async with admin_engine.connect() as conn:
        assert list(await conn.scalars(select(column).where(model.tenant_id == tenant))) == [
            "secret" if model is Chunk else "Q3 restructuring plan"
        ]
