"""Provider contract tests against mocked HTTP (respx). Verifies request shape +
that complete() returns a parseable intent dict, including the repair round-trip."""

import httpx
import pytest
import respx

from deskpet.config import LLMConfig
from deskpet.brain.providers import make_provider, ProviderAuthError
from deskpet.brain.schema import intent_json_schema
from deskpet.brain.parse import coerce_intent
from deskpet.types import Verb

SCHEMA = intent_json_schema()
GOOD = '{"thought":"x","verb":"walk_to","target":"cursor","emotion":"curious","say":null,"confidence":0.7}'


@respx.mock
def test_ollama_complete():
    cfg = LLMConfig(provider="ollama", base_url="http://127.0.0.1:11434", model="gemma4:12b")
    route = respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": GOOD}})
    )
    prov = make_provider(cfg)
    data = prov.complete("sys", "scene", None, SCHEMA)
    assert coerce_intent(data).verb is Verb.WALK_TO
    sent = route.calls[0].request
    body = sent.content.decode()
    assert '"think": false' in body or '"think":false' in body
    assert "gemma4:12b" in body


@respx.mock
def test_ollama_image_included():
    cfg = LLMConfig(provider="ollama")
    route = respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": GOOD}})
    )
    prov = make_provider(cfg)
    prov.complete("sys", "scene", b"\x89PNGfakebytes", SCHEMA)
    assert "images" in route.calls[0].request.content.decode()


@respx.mock
def test_ollama_repair_roundtrip():
    cfg = LLMConfig(provider="ollama")
    responses = [
        httpx.Response(200, json={"message": {"content": "I think the cat should walk. no json here."}}),
        httpx.Response(200, json={"message": {"content": GOOD}}),
    ]
    respx.post("http://127.0.0.1:11434/api/chat").mock(side_effect=responses)
    prov = make_provider(cfg)
    data = prov.complete("sys", "scene", None, SCHEMA)
    assert coerce_intent(data).verb is Verb.WALK_TO


@respx.mock
def test_openai_complete():
    cfg = LLMConfig(provider="openai", api_key="sk-test", model="gpt-4o")
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]})
    )
    prov = make_provider(cfg)
    assert coerce_intent(prov.complete("s", "u", None, SCHEMA)).verb is Verb.WALK_TO


@respx.mock
def test_gemini_complete():
    cfg = LLMConfig(provider="gemini", api_key="g-test", model="gemini-1.5-flash")
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    ).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": GOOD}]}}]}
        )
    )
    prov = make_provider(cfg)
    assert coerce_intent(prov.complete("s", "u", None, SCHEMA)).verb is Verb.WALK_TO


def test_cloud_requires_key():
    with pytest.raises(ProviderAuthError):
        make_provider(LLMConfig(provider="openai", api_key=""))


@respx.mock
def test_custom_openai_falls_back_to_json_object():
    cfg = LLMConfig(provider="openai_compat", base_url="http://localhost:8000", api_key="x", model="local")
    route = respx.post("http://localhost:8000/v1/chat/completions")
    route.mock(side_effect=[
        httpx.Response(400, json={"error": "json_schema unsupported"}),
        httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]}),
    ])
    prov = make_provider(cfg)
    assert coerce_intent(prov.complete("s", "u", None, SCHEMA)).verb is Verb.WALK_TO
