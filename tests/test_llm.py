from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.config import AppConfig, ConfigError
from modules.llm import LLMClient
from modules.run_logger import RunLogger
from tests.helpers import runtime_dir


PRIMARY_MODEL = "claude-sonnet-4-6"
FALLBACK_MODEL = "claude-haiku-4-5-20251001"


def _build_client(workspace: Path, *, fallback: str = FALLBACK_MODEL) -> LLMClient:
    config = AppConfig(
        project_root=workspace,
        default_input_path=workspace,
        default_model=PRIMARY_MODEL,
        fallback_model=fallback,
    )
    logger = RunLogger(workspace / "logs")
    client = LLMClient(config, logger)
    client.api_key = "sk-ant-test"
    return client


def _read_log_events(client: LLMClient) -> list[dict]:
    return [
        json.loads(line)
        for line in client.logger.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_missing_api_key_raises_config_error(monkeypatch):
    workspace = runtime_dir("llm_no_key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = AppConfig(
        project_root=workspace,
        default_input_path=workspace,
        default_model=PRIMARY_MODEL,
        fallback_model=FALLBACK_MODEL,
    )
    client = LLMClient(config, RunLogger(workspace / "logs"))
    client.api_key = ""

    with pytest.raises(ConfigError):
        client.require_api_key()


def test_complete_returns_primary_response_without_fallback():
    workspace = runtime_dir("llm_primary_ok")
    client = _build_client(workspace)
    calls: list[str] = []

    def fake_call(model: str, system: str, user: str) -> str:
        calls.append(model)
        return "primary answer"

    client._call_model = fake_call  # type: ignore[assignment]

    result = client.complete("sys", "usr")

    assert result == "primary answer"
    assert calls == [PRIMARY_MODEL]
    events = _read_log_events(client)
    assert any(e["event"] == "model_call_started" and e["model"] == PRIMARY_MODEL for e in events)
    assert any(e["event"] == "model_call_completed" and e["model"] == PRIMARY_MODEL for e in events)
    assert not any(e["event"].startswith("model_fallback") for e in events)


def test_complete_falls_back_when_primary_fails():
    workspace = runtime_dir("llm_fallback_ok")
    client = _build_client(workspace)
    calls: list[str] = []

    def fake_call(model: str, system: str, user: str) -> str:
        calls.append(model)
        if model == PRIMARY_MODEL:
            raise RuntimeError("primary boom")
        return "fallback answer"

    client._call_model = fake_call  # type: ignore[assignment]

    result = client.complete("sys", "usr")

    assert result == "fallback answer"
    assert calls == [PRIMARY_MODEL, FALLBACK_MODEL]

    events = _read_log_events(client)
    event_names = [e["event"] for e in events]
    assert "model_call_error" in event_names
    assert "model_fallback_started" in event_names
    assert "model_fallback_completed" in event_names

    error_events = [e for e in events if e["event"] == "model_call_error"]
    assert any("primary boom" in e["error"] for e in error_events)


def test_complete_raises_when_primary_and_fallback_fail():
    workspace = runtime_dir("llm_both_fail")
    client = _build_client(workspace)

    def fake_call(model: str, system: str, user: str) -> str:
        raise RuntimeError(f"{model} boom")

    client._call_model = fake_call  # type: ignore[assignment]

    with pytest.raises(ConfigError) as exc_info:
        client.complete("sys", "usr")

    message = str(exc_info.value)
    assert PRIMARY_MODEL in message
    assert FALLBACK_MODEL in message

    events = _read_log_events(client)
    error_events = [e for e in events if e["event"] == "model_call_error"]
    assert len(error_events) == 2
    assert {e["model"] for e in error_events} == {PRIMARY_MODEL, FALLBACK_MODEL}


def test_complete_raises_when_no_fallback_configured():
    workspace = runtime_dir("llm_no_fallback")
    client = _build_client(workspace, fallback="")

    def fake_call(model: str, system: str, user: str) -> str:
        raise RuntimeError("primary boom")

    client._call_model = fake_call  # type: ignore[assignment]

    with pytest.raises(ConfigError) as exc_info:
        client.complete("sys", "usr")

    assert "no fallback model" in str(exc_info.value).lower()
    events = _read_log_events(client)
    assert not any(e["event"].startswith("model_fallback") for e in events)


def test_complete_skips_fallback_when_same_as_primary():
    workspace = runtime_dir("llm_same_fallback")
    client = _build_client(workspace, fallback=PRIMARY_MODEL)

    def fake_call(model: str, system: str, user: str) -> str:
        raise RuntimeError("primary boom")

    client._call_model = fake_call  # type: ignore[assignment]

    with pytest.raises(ConfigError):
        client.complete("sys", "usr")

    events = _read_log_events(client)
    assert not any(e["event"].startswith("model_fallback") for e in events)


def test_complete_uses_explicit_model_argument_as_primary():
    workspace = runtime_dir("llm_explicit_model")
    client = _build_client(workspace)
    explicit_model = "claude-opus-4-7"
    calls: list[str] = []

    def fake_call(model: str, system: str, user: str) -> str:
        calls.append(model)
        if model == explicit_model:
            raise RuntimeError("explicit boom")
        return "fallback answer"

    client._call_model = fake_call  # type: ignore[assignment]

    result = client.complete("sys", "usr", model=explicit_model)

    assert result == "fallback answer"
    assert calls == [explicit_model, FALLBACK_MODEL]


# --- Fallback-usage summary ------------------------------------------------


def test_fallback_summary_returns_none_when_no_calls_made():
    workspace = runtime_dir("llm_summary_empty")
    client = _build_client(workspace)

    assert client.fallback_summary() is None


def test_fallback_summary_returns_none_when_only_primary_succeeded():
    workspace = runtime_dir("llm_summary_primary_only")
    client = _build_client(workspace)

    client._call_model = lambda model, system, user: "primary answer"  # type: ignore[assignment]
    client.complete("sys", "usr")
    client.complete("sys", "usr")

    # No fallback was ever needed → no notice should surface.
    assert client.fallback_summary() is None


def test_fallback_summary_reports_models_and_counts_after_fallback():
    workspace = runtime_dir("llm_summary_after_fallback")
    client = _build_client(workspace)

    def fake_call(model: str, system: str, user: str) -> str:
        if model == PRIMARY_MODEL:
            raise RuntimeError("primary boom")
        return "fallback answer"

    client._call_model = fake_call  # type: ignore[assignment]
    client.complete("sys", "usr")

    summary = client.fallback_summary()
    assert summary is not None
    assert summary["fallback_used"] is True
    assert summary["primary_model"] == PRIMARY_MODEL
    assert summary["fallback_model"] == FALLBACK_MODEL
    assert summary["primary_calls"] == 0
    assert summary["fallback_calls"] == 1
    assert summary["total_calls"] == 1


def test_fallback_summary_counts_mixed_primary_and_fallback_calls():
    workspace = runtime_dir("llm_summary_mixed")
    client = _build_client(workspace)
    call_log: list[str] = []

    def fake_call(model: str, system: str, user: str) -> str:
        call_log.append(model)
        # First primary attempt fails, subsequent primary attempts succeed.
        if model == PRIMARY_MODEL and call_log.count(PRIMARY_MODEL) == 1:
            raise RuntimeError("transient")
        if model == FALLBACK_MODEL:
            return "fallback"
        return "primary"

    client._call_model = fake_call  # type: ignore[assignment]
    # First call: primary fails → fallback succeeds (fallback_calls=1)
    client.complete("sys", "usr")
    # Second call: primary succeeds (primary_calls=1)
    client.complete("sys", "usr")

    summary = client.fallback_summary()
    assert summary is not None
    assert summary["primary_calls"] == 1
    assert summary["fallback_calls"] == 1
    assert summary["total_calls"] == 2


def test_fallback_summary_stable_when_both_models_fail():
    workspace = runtime_dir("llm_summary_both_fail")
    client = _build_client(workspace)

    def fake_call(model: str, system: str, user: str) -> str:
        raise RuntimeError(f"{model} boom")

    client._call_model = fake_call  # type: ignore[assignment]

    with pytest.raises(ConfigError):
        client.complete("sys", "usr")

    # Neither primary nor fallback completed → no notice should surface.
    assert client.fallback_summary() is None
