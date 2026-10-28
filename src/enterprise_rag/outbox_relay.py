from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_rag.constants.outbox import OUTBOX_BATCH_SIZE
from enterprise_rag.db.models import OutboxEvent

Publish = Callable[[str, dict[str, Any]], Awaitable[None]]


async def relay_once(
    session_factory: async_sessionmaker[AsyncSession],
    publish: Publish,
    *,
    batch_size: int = OUTBOX_BATCH_SIZE,
) -> int:
    """Publish up to batch_size pending events, oldest first. Returns how many were published.

    At-least-once: events are deleted only after the whole batch is published, so a failure
    mid-batch republishes the earlier ones. Consumers must be idempotent.
    SKIP LOCKED lets several relay replicas drain the table without publishing twice.
    """
    async with session_factory() as session, session.begin():
        events = (
            await session.scalars(
                select(OutboxEvent)
                .order_by(OutboxEvent.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for event in events:
            await publish(event.topic, event.payload)
        if events:
            await session.execute(
                delete(OutboxEvent).where(OutboxEvent.id.in_([e.id for e in events]))
            )
        return len(events)
