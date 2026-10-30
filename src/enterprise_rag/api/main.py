"""Deployable query API: `uvicorn enterprise_rag.api.main:app`."""

import functools

import httpx
import jwt
from openai import AsyncOpenAI

from enterprise_rag.api.app import make_app
from enterprise_rag.api.auth import verify_token
from enterprise_rag.api.generation import make_openai_generate
from enterprise_rag.api.rerank import make_tei_rerank
from enterprise_rag.config import ApiSettings
from enterprise_rag.constants.api import GENERATE_MAX_RETRIES, GENERATE_TIMEOUT_S
from enterprise_rag.constants.auth import JWKS_TIMEOUT_S
from enterprise_rag.constants.embedding import EMBED_TIMEOUT_S
from enterprise_rag.constants.retrieval import RERANK_TIMEOUT_S
from enterprise_rag.db.session import make_engine, make_session_factory
from enterprise_rag.ingestion.embedding import make_tei_embed

settings = ApiSettings()
# Caches the IdP's public keys and refetches on an unknown key id, so key rotation just works.
jwks = jwt.PyJWKClient(settings.jwks_url, timeout=JWKS_TIMEOUT_S)

app = make_app(
    make_session_factory(make_engine(settings.database_url)),
    make_tei_embed(httpx.AsyncClient(base_url=settings.tei_url, timeout=EMBED_TIMEOUT_S)),
    make_tei_rerank(httpx.AsyncClient(base_url=settings.rerank_url, timeout=RERANK_TIMEOUT_S)),
    make_openai_generate(
        AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=GENERATE_TIMEOUT_S,
            max_retries=GENERATE_MAX_RETRIES,
        ),
        settings.openai_model,
    ),
    functools.partial(
        verify_token,
        key_for=lambda token: jwks.get_signing_key_from_jwt(token).key,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    ),
)
