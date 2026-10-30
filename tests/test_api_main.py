import importlib

import httpx
import pytest

ENV = {
    "RAG_DATABASE_URL": "postgresql+asyncpg://rag_app:x@localhost:1/rag",
    "RAG_TEI_URL": "http://tei.local",
    "RAG_RERANK_URL": "http://reranker.local",
    "RAG_JWT_ISSUER": "https://idp.test",
    "RAG_JWT_AUDIENCE": "enterprise-rag",
    "RAG_JWKS_URL": "https://idp.test/.well-known/jwks.json",
    "RAG_OPENAI_MODEL": "test-model",
    "RAG_OPENAI_API_KEY": "sk-test",
}


# Break caught: the deployable API module failing at import, or serving a route without auth
# because the verifier is wired wrong; app tests build the app by hand and would not notice.
@pytest.mark.parametrize(
    "route, body", [("/v1/search", {"query": "q"}), ("/v1/answer", {"question": "q"})]
)
async def test_api_module_serves_routes_behind_auth(monkeypatch, route, body):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    main = importlib.import_module("enterprise_rag.api.main")
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.post(route, json=body)

    assert response.status_code == 401


# Break caught: the API starting with no identity provider, reranker or model configured.
@pytest.mark.parametrize(
    "missing",
    [
        "RAG_JWT_ISSUER",
        "RAG_JWT_AUDIENCE",
        "RAG_JWKS_URL",
        "RAG_RERANK_URL",
        "RAG_OPENAI_MODEL",
        "RAG_OPENAI_API_KEY",
    ],
)
def test_api_settings_require_the_identity_provider(monkeypatch, missing):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing)
    from enterprise_rag.config import ApiSettings

    with pytest.raises(ValueError):
        ApiSettings(_env_file=None)
