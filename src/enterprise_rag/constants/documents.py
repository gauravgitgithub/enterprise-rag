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

# Object keys an ingest job may read: tenants/<tenant>/docs/<doc>/v<n>/<file>. Segments cannot be
# empty, "." or "..". Anchored with re.fullmatch; {tenant} is filled with the re.escape'd id.
DOCUMENT_KEY_PATTERN = r"tenants/{tenant}/docs/[A-Za-z0-9-]+/v[0-9]+/[A-Za-z0-9][A-Za-z0-9._-]*"

# Parser limits: a single upload must not tie up a worker.
PARSE_MAX_PAGES = 500
PARSE_TIMEOUT_S = 120.0
# A DOCX is a ZIP; refuse ones that expand beyond this (zip bombs).
PARSE_MAX_UNZIPPED_BYTES = 200 * 1024 * 1024
DOCUMENT_STATUS_FAILED = "failed"
