from enterprise_rag.constants.outbox import TOPIC_DOCUMENT_INGEST

INGEST_QUEUE = "ingest"
INGEST_TASK = "enterprise_rag.ingest_document"

# Outbox topic -> (Celery task name, SQS queue name).
TOPIC_ROUTES = {TOPIC_DOCUMENT_INGEST: (INGEST_TASK, INGEST_QUEUE)}

# Longer than the slowest ingest task, or SQS redelivers it to a second worker mid-run.
VISIBILITY_TIMEOUT_S = 900
# Hard kill for a stuck task (a parser hang); below the visibility timeout, so no double run.
TASK_TIME_LIMIT_S = 600
# SQS long polling: fewer empty receives, lower cost.
SQS_WAIT_TIME_S = 20

# Transient failures (TEI, DB, S3 down) retry with exponential backoff, at most this many times.
INGEST_MAX_RETRIES = 5
