import asyncio
import hashlib
import uuid

from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Document
from enterprise_rag.repositories.uow import UnitOfWork

EVERYONE = frozenset({"everyone"})
HASH = hashlib.sha256(b"same bytes").hexdigest()


def _doc(title: str) -> Document:
    return Document(
        title=title, source_uri=f"s3://t/{uuid.uuid4()}", content_hash=HASH, acl_groups=["everyone"]
    )


# Break caught: plain INSERT -> IntegrityError, or a second row for the same content.
async def test_registering_same_content_twice_returns_the_original(app_factory, make_tenant):
    ctx = RequestContext(await make_tenant(), EVERYONE)
    async with UnitOfWork(app_factory, ctx) as uow:
        first, created_first = await uow.documents.register(_doc("v1"))
    async with UnitOfWork(app_factory, ctx) as uow:
        second, created_second = await uow.documents.register(_doc("v1 again"))

    assert (created_first, created_second) == (True, False)
    assert second.id == first.id
    assert second.title == "v1"


# Break caught: dedupe keyed on hash alone, which would also leak "this file exists elsewhere".
async def test_same_content_in_another_tenant_is_a_separate_document(app_factory, make_tenant):
    a = RequestContext(await make_tenant(), EVERYONE)
    b = RequestContext(await make_tenant(), EVERYONE)
    async with UnitOfWork(app_factory, a) as uow:
        doc_a, _ = await uow.documents.register(_doc("a"))
    async with UnitOfWork(app_factory, b) as uow:
        doc_b, created = await uow.documents.register(_doc("b"))

    assert created is True
    assert doc_b.id != doc_a.id


# Break caught: check-then-insert, where both racers pass the check and one hits the constraint.
async def test_concurrent_registrations_of_same_content_create_one_document(
    app_factory, make_tenant
):
    ctx = RequestContext(await make_tenant(), EVERYONE)

    async def register(title: str) -> tuple[Document, bool]:
        async with UnitOfWork(app_factory, ctx) as uow:
            return await uow.documents.register(_doc(title))

    results = await asyncio.gather(*(register(f"r{i}") for i in range(5)))

    assert sorted(created for _, created in results) == [False, False, False, False, True]
    assert len({doc.id for doc, _ in results}) == 1
