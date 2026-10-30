import uuid

import jwt
import pytest

from enterprise_rag.api.auth import AuthError, verify_token
from enterprise_rag.context import RequestContext
from tests.tokens import AUDIENCE, ISSUER, OTHER_KEY, key_for, mint

TENANT = uuid.uuid4()


def verify(token: str) -> RequestContext:
    return verify_token(token, key_for, issuer=ISSUER, audience=AUDIENCE)


# Break caught: tenant or groups read from somewhere other than the signed claims.
def test_valid_token_yields_its_tenant_and_groups():
    assert verify(mint(TENANT, ["hr", "eng"])) == RequestContext(TENANT, frozenset({"hr", "eng"}))


# Break caught: any of the standard checks switched off, letting a token from another
# issuer or app, an expired one, or a forged one through.
@pytest.mark.parametrize(
    "token",
    [
        mint(TENANT, ["hr"], exp=1),
        mint(TENANT, ["hr"], iss="https://evil.test"),
        mint(TENANT, ["hr"], aud="other-app"),
        mint(TENANT, ["hr"], key=OTHER_KEY),
        mint(TENANT, ["hr"], exp=None),
        # Only asymmetric algorithms: an HMAC-signed token never verifies, whatever its key.
        jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "exp": 2**31,
                "tenant_id": str(TENANT),
                "groups": ["hr"],
            },
            "s" * 32,
            algorithm="HS256",
        ),
        "not-a-jwt",
    ],
)
def test_untrusted_tokens_are_rejected(token):
    with pytest.raises(AuthError):
        verify(token)


# Break caught: malformed tenant/group claims crashing with a 500, or a string "hr" becoming
# the groups {"h", "r"}.
@pytest.mark.parametrize(
    "tenant, groups",
    [
        (None, ["hr"]),
        ("not-a-uuid", ["hr"]),
        (TENANT, None),
        (TENANT, "hr"),
        (TENANT, []),
        (TENANT, ["a,b"]),
    ],
)
def test_malformed_identity_claims_are_rejected(tenant, groups):
    with pytest.raises(AuthError):
        verify(mint(tenant or "x", groups, tenant_id=None if tenant is None else str(tenant)))


def test_signing_key_lookup_failures_are_auth_errors():
    def no_key(token: str):
        raise jwt.PyJWKClientError("kid not found")

    with pytest.raises(AuthError):
        verify_token(mint(TENANT, ["hr"]), no_key, issuer=ISSUER, audience=AUDIENCE)
