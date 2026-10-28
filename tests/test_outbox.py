import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from enterprise_rag.constants.db import TENANT_SETTING
from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import OutboxEvent
from enterprise_rag.outbox_relay import relay_once
from enterprise_rag.repositories.uow import UnitOfWork

EVERYONE = frozenset({"everyone"})


class UploadFailed(Exception):
    pass


class Sink:
    """Records what the relay published."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))

    def for_marker(self, marker: str) -> list[tuple[str, dict[str, Any]]]:
        return [e for e in self.events if e[1].get("marker") == marker]


async def _emit(app_factory, tenant: uuid.UUID, marker: str, n: int = 1) -> None:
    async with UnitOfWork(app_factory, RequestContext(tenant, EVERYONE)) as uow:
        for i in range(n):
            await uow.outbox.add("document.ingest", {"marker": marker, "i": i})


async def _drain(relay_factory, sink: Sink) -> None:
    # Bounded, so a relay that never empties the table fails the test instead of hanging it.
    for _ in range(100):
        if not await relay_once(relay_factory, sink):
            return
    raise AssertionError("outbox never drained")


# Break caught: events written outside the business transaction, or never published.
async def test_committed_event_is_published_once_and_then_removed(
    app_factory, relay_factory, admin_engine, make_tenant
):
    marker = str(uuid.uuid4())
    await _emit(app_factory, await make_tenant(), marker)
    sink = Sink()

    await _drain(relay_factory, sink)
    await _drain(relay_factory, sink)

    assert sink.for_marker(marker) == [("document.ingest", {"marker": marker, "i": 0})]
    async with admin_engine.connect() as conn:
        remaining = await conn.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.payload["marker"].astext == marker)
        )
    assert remaining == 0


# Break caught: outbox written in its own transaction, so a failed upload still triggers a job.
async def test_rolled_back_transaction_publishes_nothing(app_factory, relay_factory, make_tenant):
    marker = str(uuid.uuid4())
    tenant = await make_tenant()
    with pytest.raises(UploadFailed):
        async with UnitOfWork(app_factory, RequestContext(tenant, EVERYONE)) as uow:
            await uow.outbox.add("document.ingest", {"marker": marker})
            raise UploadFailed
    sink = Sink()

    await _drain(relay_factory, sink)

    assert sink.for_marker(marker) == []


# Break caught: event deleted before publish succeeds, losing the job when the broker is down.
async def test_failed_publish_keeps_the_event_for_the_next_run(
    app_factory, relay_factory, make_tenant
):
    marker = str(uuid.uuid4())
    await _emit(app_factory, await make_tenant(), marker)

    async def broker_down(topic: str, payload: dict[str, Any]) -> None:
        raise ConnectionError("SQS unavailable")

    with pytest.raises(ConnectionError):
        await relay_once(relay_factory, broker_down)
    sink = Sink()
    await _drain(relay_factory, sink)

    assert len(sink.for_marker(marker)) == 1


# Break caught: relays reading without row locks, so replicas publish the same event twice.
async def test_concurrent_relays_publish_each_event_once(app_factory, relay_factory, make_tenant):
    marker = str(uuid.uuid4())
    await _emit(app_factory, await make_tenant(), marker, n=20)
    sink = Sink()

    async def slow(topic: str, payload: dict[str, Any]) -> None:
        await asyncio.sleep(0.01)  # hold locks long enough for the replicas to overlap
        await sink(topic, payload)

    async def replica() -> None:
        for _ in range(100):
            if not await relay_once(relay_factory, slow, batch_size=5):
                return
        raise AssertionError("outbox never drained")

    await asyncio.gather(replica(), replica(), replica())

    assert sorted(p["i"] for _, p in sink.for_marker(marker)) == list(range(20))


# Break caught: outbox readable across tenants by the app role.
async def test_app_role_sees_only_its_own_tenants_events(app_factory, make_tenant):
    mine, theirs = await make_tenant(), await make_tenant()
    await _emit(app_factory, theirs, str(uuid.uuid4()))

    async with app_factory() as session, session.begin():
        await session.execute(select(func.set_config(TENANT_SETTING, str(mine), True)))
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
