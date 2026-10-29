"""Deployable outbox relay: `python -m enterprise_rag.relay`. Safe to run several replicas."""

import asyncio
import logging
import signal

from enterprise_rag.config import RelaySettings
from enterprise_rag.constants.queues import INGEST_QUEUE
from enterprise_rag.db.session import make_engine, make_session_factory
from enterprise_rag.outbox_relay import run_relay
from enterprise_rag.worker.celery_app import celery_publisher, make_celery


async def main() -> None:
    settings = RelaySettings()
    app = make_celery(
        region=settings.aws_region, queue_urls={INGEST_QUEUE: settings.ingest_queue_url}
    )
    engine = make_engine(settings.relay_database_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    # Kubernetes sends SIGTERM: finish the current batch, then exit.
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await run_relay(make_session_factory(engine), celery_publisher(app), stop)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
