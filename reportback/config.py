"""Runtime configuration. Model names, endpoints and dimensions live here (env-driven), never in code."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Hosts ReportBack may fetch from (CLAUDE.md: "Sources come from an allowlist only").
# Toronto Open Data CKAN, TMMIS agenda item pages, and the background files linked from them.
DEFAULT_ALLOWED_HOSTS = (
    "ckan0.cf.opendata.inter.prod-toronto.ca",
    "secure.toronto.ca",
    "www.toronto.ca",
)

LLMProvider = Literal["foundry", "openai", "ollama"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high"]


class Settings(BaseSettings):
    # env_ignore_empty: `LLM_REASONING_EFFORT=` (as in .env.example) means "unset", not "".
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    # --- LLM layer (all calls go through reportback/llm/) ---
    llm_provider: LLMProvider = "foundry"
    # Foundry/Azure OpenAI v1 endpoint, e.g. https://<resource>.openai.azure.com/openai/v1/
    # Ollama: http://localhost:11434/v1
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_chat_model: str | None = None  # deployment name on Foundry
    llm_embedding_model: str | None = None  # deployment name on Foundry
    # Reasoning models (gpt-5 family) bill hidden reasoning tokens as output. Set this for them
    # to cap cost; leave unset for non-reasoning models (they reject the parameter).
    llm_reasoning_effort: ReasoningEffort | None = None
    embedding_dim: int = 1536  # must match vector(N) columns in the schema
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2

    # --- Cost guard (Phase 3 linker) ---
    link_max_cost_usd: float = 5.0

    # --- Polite crawling ---
    ckan_base_url: str = "https://ckan0.cf.opendata.inter.prod-toronto.ca"
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    http_min_interval_s: float = Field(default=1.0, ge=1.0)  # 1 req/sec ceiling
    http_timeout_s: float = 60.0
    # Descriptive UA. Optional contact URL comes from env; no personal data is hard-coded.
    http_contact_url: str | None = None

    # --- Paths ---
    data_dir: Path = Path("data")

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def user_agent(self) -> str:
        from reportback import __version__

        contact = f"; +{self.http_contact_url}" if self.http_contact_url else ""
        return f"ReportBackTO/{__version__} (civic research on public council records{contact})"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
