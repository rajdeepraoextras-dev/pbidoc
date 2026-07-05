"""Shared pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _no_llm_cache_by_default(monkeypatch):
    """Force the LLM response cache off for every test.

    ``cli.main(["generate", ...])`` sets ``PBICOMPASS_LLM_CACHE`` via
    ``os.environ.setdefault`` to turn caching on by default for real CLI
    runs. Env var mutations are process-global, so without this fixture the
    first CLI test to run leaves the cache on (and its on-disk db populated)
    for every test that runs afterward in the same pytest process, making
    FakeLLMClient call-count assertions non-deterministic depending on test
    order. ``monkeypatch.setenv`` auto-reverts after each test.
    """
    monkeypatch.setenv("PBICOMPASS_LLM_CACHE", "off")
    monkeypatch.setenv("PBICOMPASS_SCORE_HISTORY", "off")
