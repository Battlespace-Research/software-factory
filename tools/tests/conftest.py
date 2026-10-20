"""Shared pytest fixtures for the swfactory tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_factory_home_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with FACTORY_HOME unset, so the developer's shell cannot leak in.

    A test that needs it calls ``monkeypatch.setenv("FACTORY_HOME", ...)`` itself.
    """
    monkeypatch.delenv("FACTORY_HOME", raising=False)
