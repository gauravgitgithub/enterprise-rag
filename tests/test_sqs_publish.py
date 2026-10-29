import asyncio
import base64
import json
import os
import signal
import sys
import uuid
from collections.abc import Iterator

import boto3
import pytest
from celery.contrib.testing.worker import start_worker
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from enterprise_rag.constants.db import APP_DB_ROLE, RELAY_DB_ROLE
from enterprise_rag.constants.outbox import TOPIC_DOCUMENT_INGEST
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

    # shared=False: a shared task would replace the real one in every later Celery app.
    @app.task(name=INGEST_TASK, shared=False)
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


def _task_kwargs(sqs, queue_url: str) -> list[dict]:
    """Celery task kwargs of the messages now on the queue (kombu double-encodes the body)."""
    received = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1)
    kwargs = []
    for message in received.get("Messages", []):
        envelope = json.loads(base64.b64decode(message["Body"]))
        kwargs.append(json.loads(base64.b64decode(envelope["body"]))[1])
    return kwargs


# Break caught: the deployable relay failing to start, to publish from settings, or to exit
# cleanly on SIGTERM (Kubernetes would SIGKILL it mid-batch).
async def test_relay_process_publishes_and_exits_on_sigterm(
    sqs_queue_url, migrated, app_factory, make_tenant
):
    marker = str(uuid.uuid4())
    async with UnitOfWork(
        app_factory, RequestContext(await make_tenant(), frozenset({"everyone"}))
    ) as uow:
        await uow.outbox.add(TOPIC_DOCUMENT_INGEST, {"marker": marker})
    env = os.environ | {
        "RAG_RELAY_DATABASE_URL": migrated[1].replace(f"{APP_DB_ROLE}:", f"{RELAY_DB_ROLE}:"),
        "RAG_AWS_REGION": REGION,
        "RAG_INGEST_QUEUE_URL": sqs_queue_url,
    }
    relay = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "enterprise_rag.relay", env=env
    )
    try:
        sqs = boto3.client("sqs", region_name=REGION)
        seen: list[dict] = []
        for _ in range(30):
            seen += await asyncio.to_thread(_task_kwargs, sqs, sqs_queue_url)
            if any(k.get("marker") == marker for k in seen):
                break
        assert any(k.get("marker") == marker for k in seen)

        relay.send_signal(signal.SIGTERM)
        assert await asyncio.wait_for(relay.wait(), timeout=15) == 0
    finally:
        if relay.returncode is None:
            relay.kill()
