import importlib

import httpx
import pytest

ENV = {
    "RAG_DATABASE_URL": "postgresql+asyncpg://rag_app:x@localhost:1/rag",
    "RAG_TEI_URL": "http://tei.local",
    "RAG_JWT_ISSUER": "https://idp.test",
    "RAG_JWT_AUDIENCE": "enterprise-rag",
    "RAG_JWKS_URL": "https://idp.test/.well-known/jwks.json",
}


# Break caught: the deployable API module failing at import, or serving search without auth
# because the verifier is wired wrong; app tests build the app by hand and would not notice.
async def test_api_module_serves_search_behind_auth(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    main = importlib.import_module("enterprise_rag.api.main")
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.post("/v1/search", json={"query": "q"})

    assert response.status_code == 401


# Break caught: the API starting with no identity provider configured.
@pytest.mark.parametrize("missing", ["RAG_JWT_ISSUER", "RAG_JWT_AUDIENCE", "RAG_JWKS_URL"])
def test_api_settings_require_the_identity_provider(monkeypatch, missing):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing)
    from enterprise_rag.config import ApiSettings

    with pytest.raises(ValueError):
        ApiSettings(_env_file=None)
