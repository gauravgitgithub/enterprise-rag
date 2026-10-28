import asyncio
from typing import Any
from urllib.parse import urlsplit

from enterprise_rag.constants.documents import MAX_DOCUMENT_BYTES
from enterprise_rag.ingestion.pipeline import Fetch


def make_s3_fetch(bucket: str, s3: Any, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> Fetch:
    """Fetch for the ingest pipeline. `s3` is a boto3 S3 client (SeaweedFS locally)."""

    async def fetch(uri: str) -> bytes:
        # source_uri comes from the database: never let it choose the bucket or scheme.
        parts = urlsplit(uri)
        key = parts.path.lstrip("/")
        if parts.scheme != "s3" or parts.netloc != bucket or not key:
            raise ValueError(f"refusing to read outside s3://{bucket}/: {uri}")
        # boto3 is blocking; keep it off the event loop.
        obj = await asyncio.to_thread(s3.get_object, Bucket=bucket, Key=key)
        if obj["ContentLength"] > max_bytes:
            obj["Body"].close()
            raise ValueError(f"object too large: {obj['ContentLength']} > {max_bytes} bytes")
        return await asyncio.to_thread(obj["Body"].read)

    return fetch
