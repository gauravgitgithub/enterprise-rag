"""Create the local overlay's dev identity provider key pair, if it doesn't exist yet.

Writes deploy/k8s/overlays/local/dev-idp/key.pem (private, git-ignored) and jwks.json
(public), which svc/dev-idp serves to the API. Local and CI use only.
"""

import json

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from devstack import IDP_DIR, KEY_PATH, KID

if not KEY_PATH.exists():
    IDP_DIR.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_PATH.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    KEY_PATH.chmod(0o600)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwks = {"keys": [jwk | {"kid": KID, "use": "sig", "alg": "RS256"}]}
    (IDP_DIR / "jwks.json").write_text(json.dumps(jwks, indent=2))
print(f"dev IdP keys in {IDP_DIR}")
