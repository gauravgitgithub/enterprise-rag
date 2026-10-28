from collections.abc import Iterator

import boto3
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from enterprise_rag.ingestion.storage import make_s3_fetch

BUCKET = "rag-docs-test"


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
        yield client


async def test_fetches_the_object_bytes(s3):
    s3.put_object(Bucket=BUCKET, Key="tenants/t1/docs/d1/v1/original.pdf", Body=b"%PDF-1.7 ...")

    fetch = make_s3_fetch(BUCKET, s3)

    assert await fetch(f"s3://{BUCKET}/tenants/t1/docs/d1/v1/original.pdf") == b"%PDF-1.7 ..."


# Break caught: source_uri comes from the database; trusting it lets a bad row read any bucket
# the worker's IAM role can reach, or any URL scheme.
@pytest.mark.parametrize(
    "uri",
    [
        "s3://other-bucket/tenants/t1/secret.pdf",
        "https://rag-docs-test.s3.amazonaws.com/tenants/t1/x.pdf",
        f"s3://{BUCKET}/",
    ],
)
async def test_refuses_uris_outside_the_configured_bucket(s3, uri):
    s3.put_object(Bucket="other-bucket", Key="tenants/t1/secret.pdf", Body=b"secret")

    with pytest.raises(ValueError, match="refusing"):
        await make_s3_fetch(BUCKET, s3)(uri)


# Break caught: huge objects read fully into worker memory.
async def test_refuses_objects_over_the_size_limit(s3):
    s3.put_object(Bucket=BUCKET, Key="big.bin", Body=b"x" * 11)

    with pytest.raises(ValueError, match="too large"):
        await make_s3_fetch(BUCKET, s3, max_bytes=10)(f"s3://{BUCKET}/big.bin")
