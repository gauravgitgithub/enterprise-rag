import asyncio
import re
from typing import Any
from urllib.parse import urlsplit

from enterprise_rag.constants.documents import DOCUMENT_KEY_PATTERN, MAX_DOCUMENT_BYTES
from enterprise_rag.db.models import Document
from enterprise_rag.ingestion.pipeline import Fetch


def make_s3_fetch(bucket: str, s3: Any, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> Fetch:
    """Fetch for the ingest pipeline. `s3` is a boto3 S3 client (SeaweedFS locally)."""

    async def fetch(document: Document) -> bytes:
        # source_uri is database data: it may only name this tenant's document keys in our bucket.
        parts = urlsplit(document.source_uri)
        key = parts.path[1:]
        allowed = DOCUMENT_KEY_PATTERN.format(tenant=re.escape(str(document.tenant_id)))
        if (
            parts.scheme != "s3"
            or parts.netloc != bucket
            or parts.query
            or parts.fragment
            or not re.fullmatch(allowed, key)
        ):
            raise ValueError(f"refusing to read {document.source_uri!r}")
        # boto3 is blocking; keep it off the event loop.
        obj = await asyncio.to_thread(s3.get_object, Bucket=bucket, Key=key)
        if obj["ContentLength"] > max_bytes:
            obj["Body"].close()
            raise ValueError(f"object too large: {obj['ContentLength']} > {max_bytes} bytes")
        return await asyncio.to_thread(obj["Body"].read)

    return fetch
