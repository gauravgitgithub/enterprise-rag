"""Fault injection: each dependency failing the way it fails in production."""

import uuid
from collections.abc import Sequence

import httpx
import openai
import pytest

from enterprise_rag.api.app import make_app
from enterprise_rag.db.session import make_engine, make_session_factory
from tests.test_api_search import _auth, _seed, embed, no_generate, rerank, verify

_TENANT = uuid.uuid4()
TEI = httpx.Request("POST", "http://tei/embed")
OPENAI = httpx.Request("POST", "https://api.openai.com/v1/responses")


def raising(exc: Exception):
    async def fail(*args: object) -> list:
        raise exc

    return fail


async def grounded(question: str, sources: Sequence[str]) -> str:
    return "Yes [1]."


async def no_unsupported(answer: str, sources: Sequence[str]) -> list[str]:
    return []


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


# Break caught: a dependency outage surfacing as an opaque 500 with no retry hint, so clients
# and load balancers treat a transient blip as a server bug (or retry with no backoff).
@pytest.mark.parametrize(
    "route, body, deps",
    [
        ("/v1/search", {"query": "q"}, {"embed": raising(httpx.ConnectError("refused"))}),
        ("/v1/search", {"query": "q"}, {"embed": raising(httpx.ReadTimeout("slow"))}),
        (
            "/v1/search",
            {"query": "q"},
            {
                "rerank": raising(
                    httpx.HTTPStatusError(
                        "overloaded", request=TEI, response=httpx.Response(503, request=TEI)
                    )
                )
            },
        ),
        (
            "/v1/answer",
            {"question": "q"},
            {"generate": raising(openai.APIConnectionError(request=OPENAI))},
        ),
        (
            "/v1/answer",
            {"question": "q"},
            {"generate": raising(openai.APITimeoutError(request=OPENAI))},
        ),
        (
            "/v1/answer",
            {"question": "q"},
            {
                "check_grounding": raising(
                    openai.RateLimitError(
                        "slow down", response=httpx.Response(429, request=OPENAI), body=None
                    )
                )
            },
        ),
    ],
)
async def test_dependency_failures_are_503_with_retry_after(
    app_factory, make_tenant, route, body, deps
):
    tenant = await make_tenant()
    await _seed(app_factory, tenant, ["hr"], "Refunds within 30 days.")
    wiring = {
        "embed": embed,
        "rerank": rerank,
        "generate": grounded,
        "check_grounding": no_unsupported,
    } | deps
    app = make_app(app_factory, verify=verify, **wiring)

    async with client(app) as c:
        response = await c.post(route, json=body, headers=_auth(tenant, ["hr"]))

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


# Break caught: Postgres down crashing with a 500 (asyncpg's refusal isn't a SQLAlchemy error).
async def test_database_down_is_503():
    down = make_session_factory(make_engine("postgresql+asyncpg://u:p@127.0.0.1:1/rag"))
    app = make_app(down, embed, rerank, no_generate, no_generate, verify)

    async with client(app) as c:
        response = await c.post("/v1/search", json={"query": "q"}, headers=_auth(_TENANT, ["hr"]))

    assert response.status_code == 503


# Break caught: our own misconfiguration (a bad OpenAI key) disguised as a retryable outage.
async def test_openai_auth_failure_is_not_retryable(app_factory, make_tenant):
    tenant = await make_tenant()
    await _seed(app_factory, tenant, ["hr"], "Refunds within 30 days.")
    bad_key = openai.AuthenticationError(
        "bad key", response=httpx.Response(401, request=OPENAI), body=None
    )
    app = make_app(app_factory, embed, rerank, raising(bad_key), no_unsupported, verify)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
        response = await c.post("/v1/answer", json={"question": "q"}, headers=_auth(tenant, ["hr"]))

    assert response.status_code == 500
