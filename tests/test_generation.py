import json

import httpx2
import pytest
from openai import AsyncOpenAI

from enterprise_rag.api.generation import build_input, make_openai_generate, resolve_citations


# Break caught: sources numbered from 0 (every citation off by one) or not delimited, so a
# source's text can pose as the question or as instructions.
def test_input_numbers_sources_from_one_and_delimits_them():
    assert build_input("Why?", ["alpha", "beta"]) == (
        '<source n="1">\nalpha\n</source>\n\n<source n="2">\nbeta\n</source>\n\nQuestion: Why?'
    )


# Break caught: citations not renumbered (so [k] doesn't match citations[k-1]), a repeat cite
# listed twice, or first-cited order lost.
def test_citations_are_renumbered_in_first_cited_order():
    text, cited = resolve_citations("A [3]. B [1][3]. C [3].", n_sources=4)

    assert text == "A [1]. B [2][1]. C [1]."
    assert cited == [2, 0]


def test_no_citations_means_none_cited():
    assert resolve_citations("I don't know.", n_sources=4) == ("I don't know.", [])


# Break caught: a made-up source number passed through, citing a chunk the model never saw.
@pytest.mark.parametrize("answer", ["Yes [0].", "Yes [5].", "Yes [1][99]."])
def test_citation_outside_the_sources_is_rejected(answer):
    with pytest.raises(ValueError):
        resolve_citations(answer, n_sources=4)


# Break caught: the configured model ignored, the instructions dropped, or the sources not sent.
async def test_openai_generate_sends_model_instructions_and_sources():
    requests: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "resp_1",
                "object": "response",
                "created_at": 0,
                "model": "test-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "Yes [1].", "annotations": []}],
                    }
                ],
            },
        )

    client = AsyncOpenAI(
        api_key="sk-test",
        base_url="http://openai.test/v1",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )

    answer = await make_openai_generate(client, "test-model")("Why?", ["alpha"])

    assert answer == "Yes [1]."
    [request] = requests
    assert request["model"] == "test-model"
    assert "Cite every claim" in request["instructions"]
    assert request["input"] == build_input("Why?", ["alpha"])
