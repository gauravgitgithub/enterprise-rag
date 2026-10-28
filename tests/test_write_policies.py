"""Database-level write rules. Statements bypass the repositories on purpose."""

import hashlib
import uuid

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import DBAPIError

from enterprise_rag.constants.db import GROUPS_SETTING, TENANT_SETTING
from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Chunk, Document, Tenant
from enterprise_rag.repositories.uow import UnitOfWork
from tests.conftest import onehot


async def _set_context(session, tenant: uuid.UUID, groups: str) -> None:
    await session.execute(
        select(
            func.set_config(TENANT_SETTING, str(tenant), True),
            func.set_config(GROUPS_SETTING, groups, True),
        )
    )


async def _doc(app_factory, tenant: uuid.UUID, acl: list[str]) -> Document:
    async with UnitOfWork(app_factory, RequestContext(tenant, frozenset(acl))) as uow:
        doc, _ = await uow.documents.register(
            Document(
                title="t",
                source_uri="s3://t",
                content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                acl_groups=acl,
            )
        )
        return doc


def _chunk_values(tenant: uuid.UUID, doc: Document, acl: list[str]) -> dict:
    return {
        "tenant_id": tenant,
        "document_id": doc.id,
        "ordinal": 0,
        "content": "x",
        "char_start": 0,
        "char_end": 1,
        "acl_groups": acl,
        "embedding": onehot(0),
    }


# Break caught: chunk INSERT checks tenant only, so a chunk can be more public than its document.
async def test_chunk_acl_must_equal_its_document_acl(app_factory, make_tenant):
    tenant = await make_tenant()
    doc = await _doc(app_factory, tenant, ["exec"])

    # Caller can read both the doc and the new row, so only an INSERT rule can stop this.
    async with app_factory() as session, session.begin():
        await _set_context(session, tenant, "everyone,exec")
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.execute(insert(Chunk).values(_chunk_values(tenant, doc, ["everyone"])))


# Break caught: chunks attachable to a document the caller cannot see (content injection).
async def test_cannot_attach_chunks_to_an_invisible_document(app_factory, make_tenant):
    tenant = await make_tenant()
    doc = await _doc(app_factory, tenant, ["exec"])

    async with app_factory() as session, session.begin():
        await _set_context(session, tenant, "everyone")
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.execute(insert(Chunk).values(_chunk_values(tenant, doc, ["everyone"])))


# Break caught: callers granting access to groups they are not in (planting docs in exec's view).
async def test_cannot_create_a_document_for_groups_you_are_not_in(app_factory, make_tenant):
    tenant = await make_tenant()

    async with app_factory() as session, session.begin():
        await _set_context(session, tenant, "everyone")
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.execute(
                insert(Document).values(
                    tenant_id=tenant,
                    title="fake board memo",
                    source_uri="s3://x",
                    content_hash="0" * 64,
                    acl_groups=["everyone", "exec"],
                    version=1,
                    status="pending",
                )
            )


# Break caught: UPDATE can relabel a visible document into groups the caller is not in.
async def test_cannot_relabel_a_document_into_groups_you_are_not_in(app_factory, make_tenant):
    tenant = await make_tenant()
    doc = await _doc(app_factory, tenant, ["everyone"])

    async with app_factory() as session, session.begin():
        await _set_context(session, tenant, "everyone")
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.execute(
                update(Document)
                .where(Document.id == doc.id)
                .values(acl_groups=["everyone", "exec"])
            )


# Break caught: app role can enumerate every customer.
async def test_app_role_cannot_list_tenants(app_factory, make_tenant):
    await make_tenant()

    async with app_factory() as session:
        with pytest.raises(DBAPIError, match="permission denied"):
            await session.execute(select(Tenant.name))


# Break caught: chunk writes gated by visibility only, so an "hr" member can add content to a
# document shared with exec, and exec sees it cited as that document.
async def test_cannot_add_chunks_visible_to_groups_you_are_not_in(app_factory, make_tenant):
    tenant = await make_tenant()
    doc = await _doc(app_factory, tenant, ["exec", "hr"])

    async with app_factory() as session, session.begin():
        await _set_context(session, tenant, "hr")
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.execute(insert(Chunk).values(_chunk_values(tenant, doc, ["exec", "hr"])))


# Break caught: DELETE gated by overlap, so one group can destroy a document shared with others.
async def test_cannot_delete_a_document_shared_with_groups_you_are_not_in(app_factory, make_tenant):
    tenant = await make_tenant()
    doc = await _doc(app_factory, tenant, ["exec", "hr"])

    async with UnitOfWork(app_factory, RequestContext(tenant, frozenset({"hr"}))) as uow:
        assert await uow.documents.delete(doc.id) is False


# Break caught: chunk ACLs copied once at ingest and never updated, so narrowing a document's
# ACL leaves its content searchable by the old audience.
async def test_narrowing_a_document_acl_hides_its_chunks(app_factory, make_tenant):
    tenant = await make_tenant()
    doc = await _doc(app_factory, tenant, ["everyone"])
    async with UnitOfWork(app_factory, RequestContext(tenant, frozenset({"everyone"}))) as uow:
        await uow.chunks.add_many(
            doc,
            [
                Chunk(
                    ordinal=0,
                    content="salary bands",
                    char_start=0,
                    char_end=12,
                    embedding=onehot(0),
                )
            ],
        )

    async with app_factory() as session, session.begin():
        await _set_context(session, tenant, "everyone,exec")
        await session.execute(
            update(Document).where(Document.id == doc.id).values(acl_groups=["exec"])
        )

    async with UnitOfWork(app_factory, RequestContext(tenant, frozenset({"everyone"}))) as uow:
        assert await uow.chunks.hybrid_search("salary", onehot(0)) == []
