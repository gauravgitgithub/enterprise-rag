import asyncio
import os
import uuid
from collections.abc import Iterator

import boto3
import pytest
from celery.contrib.testing.worker import start_worker
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from enterprise_rag.constants.queues import INGEST_QUEUE, INGEST_TASK
from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Document
from enterprise_rag.ingestion.service import register_document
from enterprise_rag.outbox_relay import relay_once
from enterprise_rag.repositories.uow import UnitOfWork
from enterprise_rag.worker.celery_app import celery_publisher, make_celery

REGION = "eu-west-1"


@pytest.fixture(scope="module")
def sqs_queue_url() -> Iterator[str]:
    mq = (
        DockerContainer("softwaremill/elasticmq-native:1.7.1")
        .with_exposed_ports(9324)
        .waiting_for(LogMessageWaitStrategy("started"))
    )
    with mq:
        endpoint = f"http://{mq.get_container_host_ip()}:{mq.get_exposed_port(9324)}"
        # Dummy credentials; boto3 (and kombu through it) honours the SQS endpoint override.
        os.environ.update(
            AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", AWS_ENDPOINT_URL_SQS=endpoint
        )
        sqs = boto3.client("sqs", region_name=REGION, endpoint_url=endpoint)
        yield sqs.create_queue(QueueName=INGEST_QUEUE)["QueueUrl"]


# Break caught: broker misconfigured (region, queue URL, endpoint) or topic routed to the
# wrong task, so jobs leave the outbox and never reach a worker.
async def test_relayed_event_reaches_a_celery_worker(
    sqs_queue_url, app_factory, relay_factory, make_tenant
):
    app = make_celery(region=REGION, queue_urls={INGEST_QUEUE: sqs_queue_url})
    app.conf.broker_transport_options["wait_time_seconds"] = 1  # keep worker shutdown fast
    received: list[dict] = []

    @app.task(name=INGEST_TASK)
    def ingest(**kwargs):  # type: ignore[no-untyped-def]
        received.append(kwargs)

    tenant = await make_tenant()
    async with UnitOfWork(app_factory, RequestContext(tenant, frozenset({"everyone"}))) as uow:
        doc, _ = await register_document(
            uow,
            Document(
                title="t",
                source_uri="s3://t",
                content_hash=uuid.uuid4().hex * 2,
                acl_groups=["everyone"],
            ),
        )

    with start_worker(app, pool="solo", perform_ping_check=False, queues=[INGEST_QUEUE]):
        while await relay_once(relay_factory, celery_publisher(app)):
            pass
        expected = {
            "tenant_id": str(tenant),
            "document_id": str(doc.id),
            "acl_groups": ["everyone"],
        }
        # Other tests may have left events in the shared outbox; wait for this one.
        for _ in range(150):
            if expected in received:
                break
            await asyncio.sleep(0.2)

    assert expected in received


async def test_unknown_topic_is_refused_so_the_event_stays_queued():
    app = make_celery(region=REGION, queue_urls={INGEST_QUEUE: "http://unused"})
    with pytest.raises(KeyError):
        await celery_publisher(app)("no.such.topic", {})
