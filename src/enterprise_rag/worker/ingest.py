from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_rag.ingestion.pipeline import Embed, Fetch, Parse
from enterprise_rag.ingestion.redaction import PiiRedactor


@dataclass(frozen=True, slots=True)
class IngestDeps:
    session_factory: async_sessionmaker[AsyncSession]
    fetch: Fetch
    parse: Parse
    redactor: PiiRedactor
    embed: Embed


async def handle_ingest(payload: dict[str, Any], deps: IngestDeps) -> str:
    raise NotImplementedError
