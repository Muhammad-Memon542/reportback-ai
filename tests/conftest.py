from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from reportback.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings isolated from the developer's .env and real data dir."""
    return Settings(_env_file=None, data_dir=tmp_path / "data", **overrides)  # type: ignore[call-arg]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)
