"""Provider-agnostic LLM client. Every model call in ReportBack goes through here.

All providers are reached with the OpenAI Python SDK against an OpenAI-compatible endpoint:
  * foundry: Azure AI Foundry / Azure OpenAI v1 endpoint (https://<resource>.openai.azure.com/openai/v1/)
  * openai:  api.openai.com (base_url optional)
  * ollama:  local fallback (http://localhost:11434/v1)
Model / deployment names come from config, never from code.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx2
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel

from reportback.config import Settings

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"


class LLMConfigError(RuntimeError):
    """The LLM layer is missing required configuration (endpoint, key, model)."""


class LLMResponseError(RuntimeError):
    """The model returned no usable output (refusal, empty, schema mismatch, wrong dim)."""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    usage: Usage
    latency_ms: float


@dataclass(frozen=True)
class JsonResult[T: BaseModel]:
    value: T
    model: str
    usage: Usage
    latency_ms: float


@dataclass(frozen=True)
class EmbedResult:
    vectors: list[list[float]]
    model: str
    usage: Usage
    latency_ms: float


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    return Usage(
        prompt_tokens=int(getattr(raw, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(raw, "completion_tokens", 0) or 0),
    )


class LLMClient:
    def __init__(self, settings: Settings, *, http_client: httpx2.Client | None = None) -> None:
        self.settings = settings
        base_url = settings.llm_base_url
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None

        if settings.llm_provider == "ollama":
            base_url = base_url or OLLAMA_DEFAULT_BASE_URL
            api_key = api_key or "ollama"  # Ollama ignores the key but the SDK requires one
        elif settings.llm_provider == "foundry" and not base_url:
            raise LLMConfigError(
                "LLM_BASE_URL is required for provider 'foundry' "
                "(e.g. https://<resource>.openai.azure.com/openai/v1/)"
            )
        if not api_key:
            raise LLMConfigError(f"LLM_API_KEY is required for provider '{settings.llm_provider}'")

        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            http_client=http_client,
        )

    @staticmethod
    def _require(value: str | None, env_name: str) -> str:
        if not value:
            raise LLMConfigError(f"{env_name} is not set")
        return value

    def _chat_model(self, model: str | None) -> str:
        return model or self._require(self.settings.llm_chat_model, "LLM_CHAT_MODEL")

    @staticmethod
    def _optional(temperature: float | None, max_tokens: int | None) -> dict[str, Any]:
        # Only send sampling params when asked: several current models reject `temperature`.
        opts: dict[str, Any] = {}
        if temperature is not None:
            opts["temperature"] = temperature
        if max_tokens is not None:
            opts["max_completion_tokens"] = max_tokens
        return opts

    def chat(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        model_name = self._chat_model(model)
        t0 = time.perf_counter()
        resp = self._client.chat.completions.create(
            model=model_name, messages=list(messages), **self._optional(temperature, max_tokens)
        )
        latency = (time.perf_counter() - t0) * 1000
        if not resp.choices or resp.choices[0].message.content is None:
            raise LLMResponseError("chat completion returned no content")
        return ChatResult(
            text=resp.choices[0].message.content,
            model=resp.model or model_name,
            usage=_usage(resp.usage),
            latency_ms=latency,
        )

    def chat_json[T: BaseModel](
        self,
        messages: Sequence[ChatCompletionMessageParam],
        schema: type[T],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> JsonResult[T]:
        """Structured output: the response must validate against `schema` (strict JSON schema)."""
        model_name = self._chat_model(model)
        t0 = time.perf_counter()
        resp = self._client.chat.completions.parse(
            model=model_name,
            messages=list(messages),
            response_format=schema,
            **self._optional(temperature, max_tokens),
        )
        latency = (time.perf_counter() - t0) * 1000
        if not resp.choices:
            raise LLMResponseError("structured completion returned no choices")
        msg = resp.choices[0].message
        if msg.refusal:
            raise LLMResponseError(f"model refused: {msg.refusal}")
        if msg.parsed is None:
            raise LLMResponseError("structured completion returned no parsed value")
        return JsonResult(
            value=msg.parsed, model=resp.model or model_name, usage=_usage(resp.usage), latency_ms=latency
        )

    def embed(self, texts: Sequence[str], *, model: str | None = None) -> EmbedResult:
        model_name = model or self._require(self.settings.llm_embedding_model, "LLM_EMBEDDING_MODEL")
        if not texts:
            return EmbedResult(vectors=[], model=model_name, usage=Usage(), latency_ms=0.0)
        t0 = time.perf_counter()
        resp = self._client.embeddings.create(model=model_name, input=list(texts))
        latency = (time.perf_counter() - t0) * 1000
        vectors = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        if len(vectors) != len(texts):
            raise LLMResponseError(f"expected {len(texts)} embeddings, got {len(vectors)}")
        dim = self.settings.embedding_dim
        bad = {len(v) for v in vectors if len(v) != dim}
        if bad:
            raise LLMResponseError(
                f"embedding dim {sorted(bad)} != EMBEDDING_DIM={dim}; the schema's vector({dim}) "
                "columns would reject these. Fix the model or EMBEDDING_DIM (and the migration)."
            )
        return EmbedResult(
            vectors=vectors,
            model=resp.model or model_name,
            usage=Usage(prompt_tokens=int(resp.usage.prompt_tokens) if resp.usage else 0),
            latency_ms=latency,
        )

    def close(self) -> None:
        self._client.close()
