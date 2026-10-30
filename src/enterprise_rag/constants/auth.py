import re

# Groups travel to Postgres as one separator-joined setting, so the charset is restricted.
GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")

# Asymmetric only: the API holds public keys, so it can verify tokens but never mint them.
# Pinning the list also blocks algorithm confusion (an HS256 token keyed with the public key).
JWT_ALGORITHMS = ["RS256", "ES256"]
JWT_REQUIRED_CLAIMS = ["exp", "iss", "aud", "tenant_id", "groups"]
# Seconds of clock skew tolerated between the identity provider and the API.
JWT_LEEWAY_S = 30
