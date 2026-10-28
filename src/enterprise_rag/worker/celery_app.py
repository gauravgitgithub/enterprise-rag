import asyncio
from typing import Any

from celery import Celery

from enterprise_rag.constants.queues import (
    INGEST_QUEUE,
    SQS_WAIT_TIME_S,
    TOPIC_ROUTES,
    VISIBILITY_TIMEOUT_S,
)
from enterprise_rag.outbox_relay import Publish


def make_celery(*, region: str, queue_urls: dict[str, str]) -> Celery:
    """Celery on SQS. Locally, AWS_ENDPOINT_URL_SQS points boto3 (and kombu) at ElasticMQ."""
    app = Celery("enterprise_rag", broker="sqs://")
    app.conf.update(
        broker_transport_options={
            "region": region,
            # Known queues only: no ListQueues/CreateQueue IAM permissions needed.
            "predefined_queues": {name: {"url": url} for name, url in queue_urls.items()},
            "visibility_timeout": VISIBILITY_TIMEOUT_S,
            "wait_time_seconds": SQS_WAIT_TIME_S,
        },
        task_default_queue=INGEST_QUEUE,
        # Ack after the task finishes, so a crashed worker's job is redelivered, not lost.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
    )
    return app


def celery_publisher(app: Celery) -> Publish:
    async def publish(topic: str, payload: dict[str, Any]) -> None:
        task, queue = TOPIC_ROUTES[topic]  # unknown topic raises: the event stays in the outbox
        # send_task does blocking network I/O; keep it off the event loop.
        await asyncio.to_thread(app.send_task, task, kwargs=payload, queue=queue)

    return publish
