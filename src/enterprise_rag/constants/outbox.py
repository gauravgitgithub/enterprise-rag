TOPIC_MAX_LEN = 100
# Events published per relay transaction; bounds how long row locks are held.
OUTBOX_BATCH_SIZE = 100

TOPIC_DOCUMENT_INGEST = "document.ingest"
# Seconds the relay waits when the outbox is empty (or a pass failed) before polling again.
RELAY_IDLE_S = 1.0
