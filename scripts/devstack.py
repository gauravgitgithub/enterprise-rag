"""Shared settings for scripts that drive the local Colima stack through port-forwards."""

import time
import uuid
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization

OVERLAY = Path("deploy/k8s/overlays/local")
IDP_DIR = OVERLAY / "dev-idp"
KEY_PATH = IDP_DIR / "key.pem"
KID = "dev-1"
ISSUER = "http://dev-idp"
AUDIENCE = "enterprise-rag"
GROUP = "everyone"

# kubectl -n rag port-forward svc/api 8000:80 / svc/postgres 15432:5432 / svc/seaweedfs 18333:8333
API_URL = "http://localhost:8000"
PG_HOST = "localhost:15432"
S3_URL = "http://localhost:18333"


def secrets() -> dict[str, str]:
    lines = (OVERLAY / "secrets.env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


def mint(tenant: uuid.UUID, groups: list[str] | None = None, ttl_s: int = 300) -> str:
    """A token the API trusts: signed with the dev IdP key that svc/dev-idp publishes."""
    key: Any = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "load-test",
        "exp": int(time.time()) + ttl_s,
        "tenant_id": str(tenant),
        "groups": groups or [GROUP],
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})
