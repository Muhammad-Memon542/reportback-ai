from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel

from reportback.llm import LLMClient, LLMConfigError, LLMResponseError
from tests.conftest import make_settings

BASE = "https://example-resource.openai.azure.com/openai/v1/"


class ItemRef(BaseModel):
    item_id: str
    meeting_year: int


def completion(content: str | None, refusal: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }


def embeddings(vectors: list[list[float]]) -> dict[str, Any]:
    # Returned out of order on purpose: the client must sort by index.
    data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)]
    return {
        "object": "list",
        "model": "test-embed",
        "data": list(reversed(data)),
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


class Recorder:
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, r: httpx2.Request) -> httpx2.Response:
        body = json.loads(r.content) if r.content else {}
        self.requests.append((r.url.path, body))
        for suffix, resp in self.responses.items():
            if r.url.path.endswith(suffix):
                return httpx2.Response(200, json=resp)
        return httpx2.Response(404, json={"error": {"message": "no route"}})


def make_client(tmp_path: Path, rec: Recorder, **overrides: Any) -> LLMClient:
    defaults: dict[str, Any] = {
        "llm_provider": "foundry",
        "llm_base_url": BASE,
        "llm_api_key": "test-key",
        "llm_chat_model": "chat-deployment",
        "llm_embedding_model": "embed-deployment",
        "embedding_dim": 4,
        "llm_max_retries": 0,
    }
    s = make_settings(tmp_path, **(defaults | overrides))
    return LLMClient(s, http_client=httpx2.Client(transport=httpx2.MockTransport(rec)))


def test_chat_uses_configured_deployment_and_omits_temperature(tmp_path: Path) -> None:
    rec = Recorder({"/chat/completions": completion("pong")})
    r = make_client(tmp_path, rec).chat([{"role": "user", "content": "ping"}])
    assert r.text == "pong"
    assert r.usage.total_tokens == 14
    path, body = rec.requests[0]
    assert path == "/openai/v1/chat/completions"
    assert body["model"] == "chat-deployment"
    assert "temperature" not in body and "max_completion_tokens" not in body


def test_chat_json_sends_strict_schema_and_parses(tmp_path: Path) -> None:
    rec = Recorder({"/chat/completions": completion('{"item_id": "2024.EX14.3", "meeting_year": 2024}')})
    r = make_client(tmp_path, rec).chat_json([{"role": "user", "content": "x"}], ItemRef)
    assert r.value == ItemRef(item_id="2024.EX14.3", meeting_year=2024)
    fmt = rec.requests[0][1]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True


def test_chat_json_refusal_raises(tmp_path: Path) -> None:
    rec = Recorder({"/chat/completions": completion(None, refusal="I can't help with that.")})
    with pytest.raises(LLMResponseError, match="refused"):
        make_client(tmp_path, rec).chat_json([{"role": "user", "content": "x"}], ItemRef)


def test_embed_sorts_by_index_and_checks_dim(tmp_path: Path) -> None:
    rec = Recorder({"/embeddings": embeddings([[1.0, 0, 0, 0], [0, 1.0, 0, 0]])})
    r = make_client(tmp_path, rec).embed(["a", "b"])
    assert r.vectors == [[1.0, 0, 0, 0], [0, 1.0, 0, 0]]
    assert rec.requests[0][1]["model"] == "embed-deployment"


def test_embed_wrong_dim_raises(tmp_path: Path) -> None:
    rec = Recorder({"/embeddings": embeddings([[1.0, 0, 0]])})
    with pytest.raises(LLMResponseError, match="EMBEDDING_DIM=4"):
        make_client(tmp_path, rec).embed(["a"])


def test_embed_empty_input_makes_no_call(tmp_path: Path) -> None:
    rec = Recorder({})
    assert make_client(tmp_path, rec).embed([]).vectors == []
    assert rec.requests == []


def test_foundry_requires_base_url(tmp_path: Path) -> None:
    s = make_settings(tmp_path, llm_provider="foundry", llm_api_key="k")
    with pytest.raises(LLMConfigError, match="LLM_BASE_URL"):
        LLMClient(s)


def test_missing_key_raises(tmp_path: Path) -> None:
    s = make_settings(tmp_path, llm_provider="openai")
    with pytest.raises(LLMConfigError, match="LLM_API_KEY"):
        LLMClient(s)


def test_missing_model_raises(tmp_path: Path) -> None:
    rec = Recorder({})
    with pytest.raises(LLMConfigError, match="LLM_CHAT_MODEL"):
        make_client(tmp_path, rec, llm_chat_model=None).chat([{"role": "user", "content": "x"}])


def test_ollama_needs_no_key_and_defaults_to_localhost(tmp_path: Path) -> None:
    s = make_settings(tmp_path, llm_provider="ollama", llm_chat_model="llama3.2")
    c = LLMClient(s)
    assert str(c._client.base_url).startswith("http://localhost:11434/v1")
