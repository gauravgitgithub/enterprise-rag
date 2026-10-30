import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.test"
AUDIENCE = "enterprise-rag"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def key_for(token: str) -> Any:
    return KEY.public_key()


def mint(tenant: uuid.UUID | str, groups: Any, *, key: Any = KEY, **overrides: Any) -> str:
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "exp": int(time.time()) + 300,
        "tenant_id": str(tenant),
        "groups": groups,
    } | overrides
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")
