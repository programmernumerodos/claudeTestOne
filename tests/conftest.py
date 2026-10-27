"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # make `fakes` importable

from fakes import FakeHTTP  # noqa: E402


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeHTTP:
    """All HTTP served by offline fakes; cache + history isolated in tmp_path."""
    return FakeHTTP().install(monkeypatch, tmp_path)


@pytest.fixture
def cfg() -> dict[str, Any]:
    from flweatherops.config import DEFAULT_CONFIG
    import copy

    return copy.deepcopy(DEFAULT_CONFIG)
