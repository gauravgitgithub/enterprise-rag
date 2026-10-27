import re

# Groups travel to Postgres as one separator-joined setting, so the charset is restricted.
GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
