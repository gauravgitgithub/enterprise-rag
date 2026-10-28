import uuid
from collections.abc import Iterator

import boto3
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from enterprise_rag.db.models import Document
from enterprise_rag.ingestion.storage import make_s3_fetch

BUCKET = "rag-docs-test"
TENANT = uuid.UUID("01000000-0000-7000-8000-000000000001")
OTHER_TENANT = uuid.UUID("01000000-0000-7000-8000-000000000002")
KEY = f"tenants/{TENANT}/docs/d1/v1/original.pdf"


def doc(uri: str, tenant: uuid.UUID = TENANT) -> Document:
    return Document(tenant_id=tenant, source_uri=uri)


@pytest.fixture(scope="module")
def s3() -> Iterator[object]:
    seaweed = (
        DockerContainer("chrislusf/seaweedfs:4.50")
        .with_command("server -s3 -ip.bind=0.0.0.0")
        .with_exposed_ports(8333)
        .waiting_for(LogMessageWaitStrategy("connected to filers"))
    )
    with seaweed:
        client = boto3.client(
            "s3",
            endpoint_url=f"http://{seaweed.get_container_host_ip()}:{seaweed.get_exposed_port(8333)}",
            region_name="us-east-1",
            aws_access_key_id="x",
            aws_secret_access_key="x",
        )
        client.create_bucket(Bucket=BUCKET)
        client.create_bucket(Bucket="other-bucket")
        client.put_object(Bucket=BUCKET, Key=KEY, Body=b"%PDF-1.7 ...")
        client.put_object(
            Bucket=BUCKET, Key=f"tenants/{OTHER_TENANT}/docs/d9/v1/secret.pdf", Body=b"secret"
        )
        yield client


async def test_fetches_the_object_bytes(s3):
    assert await make_s3_fetch(BUCKET, s3)(doc(f"s3://{BUCKET}/{KEY}")) == b"%PDF-1.7 ..."


# Break caught: source_uri is database data. Trusting it lets a bad or tampered row read another
# bucket, another tenant's files, or escape the key layout through odd segments or URL parts.
@pytest.mark.parametrize(
    "uri",
    [
        f"s3://other-bucket/{KEY}",
        f"https://{BUCKET}.s3.amazonaws.com/{KEY}",
        f"s3://{BUCKET}/tenants/{OTHER_TENANT}/docs/d9/v1/secret.pdf",
        f"s3://{BUCKET}/tenants/{TENANT}/docs/d1/v1/../../../{OTHER_TENANT}/docs/d9/v1/secret.pdf",
        f"s3://{BUCKET}/tenants/{TENANT}/docs/d1/v1/..",
        f"s3://{BUCKET}/{KEY}?versionId=1",
        f"s3://{BUCKET}/{KEY}#frag",
        f"s3://user@{BUCKET}/{KEY}",
        f"s3://{BUCKET}/",
    ],
)
async def test_refuses_anything_but_this_tenants_document_keys(s3, uri):
    with pytest.raises(ValueError, match="refusing"):
        await make_s3_fetch(BUCKET, s3)(doc(uri))


# Break caught: huge objects read fully into worker memory.
async def test_refuses_objects_over_the_size_limit(s3):
    with pytest.raises(ValueError, match="too large"):
        await make_s3_fetch(BUCKET, s3, max_bytes=5)(doc(f"s3://{BUCKET}/{KEY}"))
