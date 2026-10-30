import uuid
from collections.abc import Callable
from typing import Any

import jwt

from enterprise_rag.constants.auth import JWT_ALGORITHMS, JWT_LEEWAY_S, JWT_REQUIRED_CLAIMS
from enterprise_rag.context import RequestContext

# Returns the public key that should have signed this token (in production, from the IdP's JWKS).
KeyFor = Callable[[str], Any]


class AuthError(Exception):
    """The caller is not authenticated. The message is for logs, not for the response."""


def verify_token(token: str, key_for: KeyFor, *, issuer: str, audience: str) -> RequestContext:
    """Verify an access token and return who it speaks for. Raises AuthError."""
    try:
        claims = jwt.decode(
            token,
            key_for(token),
            algorithms=JWT_ALGORITHMS,
            issuer=issuer,
            audience=audience,
            leeway=JWT_LEEWAY_S,
            options={"require": JWT_REQUIRED_CLAIMS},
        )
        groups = claims["groups"]
        if not isinstance(groups, list):
            raise AuthError(f"groups claim must be a list, got {type(groups).__name__}")
        return RequestContext(uuid.UUID(claims["tenant_id"]), frozenset(groups))
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    except (ValueError, TypeError, AttributeError) as exc:  # bad tenant_id or group names
        raise AuthError(f"malformed identity claims: {exc!r}") from exc
