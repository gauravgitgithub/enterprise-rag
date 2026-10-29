# Inputs per TEI /embed request. TEI rejects batches above its --max-client-batch-size (32).
EMBED_BATCH_SIZE = 32
# Seconds per TEI request; a full batch on CPU takes several seconds.
EMBED_TIMEOUT_S = 60.0
