DOCUMENT_STATUS_PENDING = "pending"

TITLE_MAX_LEN = 500
TENANT_NAME_MAX_LEN = 200
CONTENT_HASH_LEN = 64  # sha256 hex
STATUS_MAX_LEN = 20

# Joins section texts into the canonical document text that chunk spans index into.
SECTION_SEPARATOR = "\n\n"
DOCUMENT_STATUS_READY = "ready"

# Upload ceiling: larger objects are refused before download, so one file cannot exhaust
# a worker's memory.
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
