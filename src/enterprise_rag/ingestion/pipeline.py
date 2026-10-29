import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from enterprise_rag.constants.chunking import CHUNK_MAX_CHARS, CHUNK_OVERLAP_CHARS
from enterprise_rag.constants.documents import DOCUMENT_STATUS_READY
from enterprise_rag.db.models import Chunk, Document
from enterprise_rag.ingestion.chunking import Section, chunk_sections
from enterprise_rag.ingestion.redaction import PiiRedactor, redact_sections
from enterprise_rag.repositories.uow import UnitOfWork

# Gets the whole document so adapters can check source_uri against its tenant.
Fetch = Callable[[Document], Awaitable[bytes]]
# (raw bytes, file name); parsers detect the format from the name.
Parse = Callable[[bytes, str], list[Section]]
Embed = Callable[[Sequence[str]], Awaitable[list[list[float]]]]


async def ingest_document(
    uow: UnitOfWork,
    document_id: uuid.UUID,
    *,
    fetch: Fetch,
    parse: Parse,
    redactor: PiiRedactor,
    embed: Embed,
) -> int:
    """Fetch, parse, redact, chunk, embed and store one document. Returns chunks written.

    Runs inside the caller's UnitOfWork, so a failure at any step writes nothing.
    Safe under SQS redelivery: a missing or already-ready document is a no-op.
    """
    document = await uow.documents.get(document_id)
    if document is None or document.status == DOCUMENT_STATUS_READY:
        return 0

    raw = await fetch(document)
    filename = PurePosixPath(urlsplit(document.source_uri).path).name
    # Parsing is CPU-bound; keep it off the event loop.
    sections = await asyncio.to_thread(parse, raw, filename)
    redacted = redact_sections(sections, redactor)
    spans = chunk_sections(
        redacted.sections, max_chars=CHUNK_MAX_CHARS, overlap=CHUNK_OVERLAP_CHARS
    )
    vectors = await embed([span.text for span in spans])

    await uow.chunks.add_many(
        document,
        [
            Chunk(
                ordinal=span.ordinal,
                content=span.text,
                section_path=span.section_path,
                page=span.page,
                char_start=span.char_start,
                char_end=span.char_end,
                embedding=vector,
            )
            for span, vector in zip(spans, vectors, strict=True)
        ],
    )
    document.status = DOCUMENT_STATUS_READY
    return len(spans)
