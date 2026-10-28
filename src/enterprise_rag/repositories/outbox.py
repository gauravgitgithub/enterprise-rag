from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import OutboxEvent


class OutboxRepository:
    def __init__(self, session: AsyncSession, ctx: RequestContext) -> None:
        self._session = session
        self._ctx = ctx

    async def add(self, topic: str, payload: dict[str, Any]) -> None:
        """Queue an event in the caller's transaction: it exists only if the transaction commits."""
        self._session.add(OutboxEvent(tenant_id=self._ctx.tenant_id, topic=topic, payload=payload))
        await self._session.flush()
