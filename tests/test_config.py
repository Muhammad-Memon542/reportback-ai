from __future__ import annotations

import pytest

from reportback.config import Settings


def test_empty_env_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example ships `LLM_REASONING_EFFORT=` and `LLM_CHAT_MODEL=` with empty values.
    monkeypatch.setenv("LLM_REASONING_EFFORT", "")
    monkeypatch.setenv("LLM_CHAT_MODEL", "")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_reasoning_effort is None
    assert s.llm_chat_model is None


def test_user_agent_has_no_personal_data() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.user_agent.startswith("ReportBackTO/")
    assert "@" not in s.user_agent
