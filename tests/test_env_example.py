"""Locks in the .env.example contract: documents ANTHROPIC_API_KEY and is
explicit about which features require it.

If a future contributor accidentally removes the key declaration, the per-feature
documentation, or re-adds an obsolete provider (OPENAI_*), these tests catch it.
"""
from __future__ import annotations

from pathlib import Path

import pytest


ENV_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / ".env.example"


@pytest.fixture(scope="module")
def env_example_text() -> str:
    return ENV_EXAMPLE_PATH.read_text(encoding="utf-8")


def test_env_example_file_exists() -> None:
    assert ENV_EXAMPLE_PATH.exists(), ".env.example must exist for new users"


def test_declares_anthropic_api_key(env_example_text: str) -> None:
    lines = [line for line in env_example_text.splitlines() if not line.lstrip().startswith("#")]
    declarations = [line for line in lines if "=" in line]
    keys = {line.split("=", 1)[0].strip() for line in declarations}
    assert "ANTHROPIC_API_KEY" in keys


def test_key_value_is_empty_placeholder(env_example_text: str) -> None:
    """The example must not ship a real key. Value after '=' must be empty."""
    for line in env_example_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ANTHROPIC_API_KEY="):
            _, value = stripped.split("=", 1)
            assert value.strip() == "", "Example must not contain a real API key"
            return
    pytest.fail("ANTHROPIC_API_KEY declaration not found")


def test_no_obsolete_openai_key(env_example_text: str) -> None:
    """Project uses Anthropic, not OpenAI — OPENAI_API_KEY should not appear
    as an active declaration."""
    for line in env_example_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("OPENAI_API_KEY"):
            pytest.fail("OPENAI_API_KEY is obsolete — project uses Anthropic")


def test_documents_features_needing_key(env_example_text: str) -> None:
    """The comment block must name at least one feature that requires the key."""
    lowered = env_example_text.lower()
    assert "review" in lowered
    assert "launch" in lowered


def test_documents_features_without_key(env_example_text: str) -> None:
    """The comment block must make clear that the QA gate runs without a key."""
    lowered = env_example_text.lower()
    assert "qa" in lowered
    assert "kein key" in lowered or "ohne" in lowered or "no key" in lowered


def test_mentions_console_link(env_example_text: str) -> None:
    """Users must know where to obtain the key."""
    assert "console.anthropic.com" in env_example_text
