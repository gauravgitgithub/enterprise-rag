import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_rag.constants.documents import (
    DOCUMENT_STATUS_FAILED,
    DOCUMENT_STATUS_PENDING,
    DOCUMENT_STATUS_READY,
)
from enterprise_rag.context import RequestContext
from enterprise_rag.ingestion.parsing import UnparseableDocument
from enterprise_rag.ingestion.pipeline import Embed, Fetch, Parse, ingest_document
from enterprise_rag.ingestion.redaction import PiiRedactor
from enterprise_rag.repositories.uow import UnitOfWork

SKIPPED = "skipped"

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestDeps:
    session_factory: async_sessionmaker[AsyncSession]
    fetch: Fetch
    parse: Parse
    redactor: PiiRedactor
    embed: Embed


def _parse_message(payload: dict[str, Any]) -> tuple[RequestContext, uuid.UUID]:
    """Queue messages are outside input: validate before acting on them. Raises ValueError."""
    try:
        groups = payload["acl_groups"]
        if not isinstance(groups, list):
            raise ValueError(f"acl_groups must be a list, got {type(groups).__name__}")
        ctx = RequestContext(uuid.UUID(payload["tenant_id"]), frozenset(groups))
        return ctx, uuid.UUID(payload["document_id"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"malformed ingest message: {exc!r}") from exc


async def handle_ingest(
    payload: dict[str, Any], deps: IngestDeps, *, last_attempt: bool = False
) -> str:
    """Ingest one document as its own tenant and groups. Returns "ready", "failed" or "skipped".

    Unparseable files are marked failed once, not retried. Any other error propagates so the
    task retries, and the transaction rolls back, leaving the document pending; on the last
    attempt it marks the document failed instead, so it never stays pending with no retry left.
    """
    ctx, document_id = _parse_message(payload)
    try:
        async with UnitOfWork(deps.session_factory, ctx) as uow:
            document = await uow.documents.get(document_id)
            # Redelivered, deleted, or already failed: nothing to do.
            if document is None or document.status != DOCUMENT_STATUS_PENDING:
                return SKIPPED
            await ingest_document(
                uow,
                document_id,
                fetch=deps.fetch,
                parse=deps.parse,
                redactor=deps.redactor,
                embed=deps.embed,
            )
            return DOCUMENT_STATUS_READY
    except UnparseableDocument:
        log.warning("document %s is unparseable; marking failed", document_id, exc_info=True)
    except Exception:
        if not last_attempt:
            raise
        log.exception("document %s failed on its last attempt; marking failed", document_id)

    async with UnitOfWork(deps.session_factory, ctx) as uow:
        document = await uow.documents.get(document_id)
        if document is not None:
            document.status = DOCUMENT_STATUS_FAILED
    return DOCUMENT_STATUS_FAILED
