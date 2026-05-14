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


def _build_client(
    workspace: Path,
    *,
    fallback: str = FALLBACK_MODEL,
    retry_attempts: int = 1,
    retry_backoff_seconds: float = 0.0,
) -> LLMClient:
    config = AppConfig(
        project_root=workspace,
        default_input_path=workspace,
        default_model=PRIMARY_MODEL,
        fallback_model=fallback,
        llm_retry_attempts=retry_attempts,
        llm_retry_backoff_seconds=retry_backoff_seconds,
    )
    logger = RunLogger(workspace / "logs")
    client = LLMClient(config, logger)
    client.api_key = "sk-ant-test"
    # Tests must never actually sleep — instance-level override of the
    # class default so `time.sleep` is bypassed during backoff windows.
    client._sleep = lambda _seconds: None  # type: ignore[method-assign]
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


# --- Retry-with-backoff before fallback -----------------------------------


def test_retry_succeeds_on_second_primary_attempt_without_fallback():
    """A transient primary error should not drop the run to Haiku immediately."""
    workspace = runtime_dir("llm_retry_primary_ok")
    client = _build_client(workspace, retry_attempts=2)
    calls: list[str] = []

    def fake_call(model: str, system: str, user: str) -> str:
        calls.append(model)
        if model == PRIMARY_MODEL and calls.count(PRIMARY_MODEL) == 1:
            raise RuntimeError("transient rate-limit")
        return "primary answer"

    client._call_model = fake_call  # type: ignore[assignment]

    result = client.complete("sys", "usr")

    assert result == "primary answer"
    # Primary called twice (one retry), fallback never reached.
    assert calls == [PRIMARY_MODEL, PRIMARY_MODEL]
    assert client._primary_calls == 1
    assert client._fallback_calls == 0
    assert client.fallback_summary() is None

    events = _read_log_events(client)
    event_names = [e["event"] for e in events]
    assert "model_call_retry" in event_names
    assert "model_call_completed" in event_names
    assert not any(e["event"].startswith("model_fallback") for e in events)


def test_retry_exhausts_primary_attempts_before_switching_to_fallback():
    workspace = runtime_dir("llm_retry_exhaust_primary")
    client = _build_client(workspace, retry_attempts=2)
    calls: list[str] = []

    def fake_call(model: str, system: str, user: str) -> str:
        calls.append(model)
        if model == PRIMARY_MODEL:
            raise RuntimeError("primary down")
        return "fallback answer"

    client._call_model = fake_call  # type: ignore[assignment]

    result = client.complete("sys", "usr")

    assert result == "fallback answer"
    # Both primary attempts exhausted, then fallback (1 attempt — also retried,
    # but the first call succeeds so only one fallback call happens).
    assert calls == [PRIMARY_MODEL, PRIMARY_MODEL, FALLBACK_MODEL]

    events = _read_log_events(client)
    error_events = [e for e in events if e["event"] == "model_call_error"]
    primary_errors = [e for e in error_events if e["model"] == PRIMARY_MODEL]
    assert len(primary_errors) == 2
    # Each retry log records the attempt number and (non-negative) wait.
    retry_events = [e for e in events if e["event"] == "model_call_retry"]
    assert any(
        e["model"] == PRIMARY_MODEL and e["next_attempt"] == 2 for e in retry_events
    )


def test_retry_also_applied_to_fallback_model():
    workspace = runtime_dir("llm_retry_fallback_too")
    client = _build_client(workspace, retry_attempts=2)
    fallback_calls = 0

    def fake_call(model: str, system: str, user: str) -> str:
        nonlocal fallback_calls
        if model == PRIMARY_MODEL:
            raise RuntimeError("primary down")
        fallback_calls += 1
        if fallback_calls == 1:
            raise RuntimeError("fallback transient")
        return "fallback answer"

    client._call_model = fake_call  # type: ignore[assignment]

    result = client.complete("sys", "usr")

    assert result == "fallback answer"
    assert fallback_calls == 2  # one retry on the fallback model too

    events = _read_log_events(client)
    retry_events = [e for e in events if e["event"] == "model_call_retry"]
    # One retry per model, labelled accordingly.
    labels = {e["label"] for e in retry_events}
    assert labels == {"primary", "fallback"}


def test_retry_uses_exponential_backoff_sleep():
    workspace = runtime_dir("llm_retry_backoff")
    client = _build_client(workspace, retry_attempts=3, retry_backoff_seconds=0.5)
    waits: list[float] = []
    client._sleep = lambda seconds: waits.append(seconds)  # type: ignore[method-assign]

    def fake_call(model: str, system: str, user: str) -> str:
        raise RuntimeError("always fail")

    client._call_model = fake_call  # type: ignore[assignment]

    with pytest.raises(ConfigError):
        client.complete("sys", "usr")

    # Primary (3 attempts → 2 sleeps): 0.5, 1.0
    # Fallback (3 attempts → 2 sleeps): 0.5, 1.0
    assert waits == [0.5, 1.0, 0.5, 1.0]


def test_retry_does_not_sleep_when_backoff_is_zero():
    workspace = runtime_dir("llm_retry_zero_backoff")
    client = _build_client(workspace, retry_attempts=3, retry_backoff_seconds=0.0)
    waits: list[float] = []
    client._sleep = lambda seconds: waits.append(seconds)  # type: ignore[method-assign]

    def fake_call(model: str, system: str, user: str) -> str:
        raise RuntimeError("always fail")

    client._call_model = fake_call  # type: ignore[assignment]

    with pytest.raises(ConfigError):
        client.complete("sys", "usr")

    # No sleep calls when backoff is 0 — keeps fast paths fast.
    assert waits == []


def test_retry_does_not_apply_to_config_error():
    """ConfigError (anthropic missing, etc.) is hard and must not be retried."""
    workspace = runtime_dir("llm_retry_no_config_retry")
    client = _build_client(workspace, retry_attempts=3)
    call_count = 0

    def fake_call(model: str, system: str, user: str) -> str:
        nonlocal call_count
        call_count += 1
        raise ConfigError("anthropic package is not installed")

    client._call_model = fake_call  # type: ignore[assignment]

    with pytest.raises(ConfigError) as exc_info:
        client.complete("sys", "usr")

    # Exactly one call attempt total — ConfigError is propagated immediately,
    # no fallback (it would hit the same hard import error), no retry.
    assert call_count == 1
    assert "not installed" in str(exc_info.value)


def test_retry_log_event_records_attempt_and_label():
    workspace = runtime_dir("llm_retry_log_shape")
    client = _build_client(workspace, retry_attempts=2)
    call_log: list[str] = []

    def fake_call(model: str, system: str, user: str) -> str:
        call_log.append(model)
        if model == PRIMARY_MODEL and call_log.count(PRIMARY_MODEL) == 1:
            raise RuntimeError("transient")
        return "ok"

    client._call_model = fake_call  # type: ignore[assignment]

    client.complete("sys", "usr")

    events = _read_log_events(client)
    error_events = [e for e in events if e["event"] == "model_call_error"]
    assert len(error_events) == 1
    err = error_events[0]
    assert err["attempt"] == 1
    assert err["max_attempts"] == 2
    assert err["label"] == "primary"

    retry_events = [e for e in events if e["event"] == "model_call_retry"]
    assert len(retry_events) == 1
    retry = retry_events[0]
    assert retry["next_attempt"] == 2
    assert retry["wait_seconds"] == 0  # zero backoff in test client
    assert retry["label"] == "primary"


def test_retry_attempts_clamped_to_minimum_one():
    """``llm_retry_attempts <= 0`` must not loop zero times."""
    workspace = runtime_dir("llm_retry_clamp")
    client = _build_client(workspace, retry_attempts=0)
    calls: list[str] = []

    def fake_call(model: str, system: str, user: str) -> str:
        calls.append(model)
        return "ok"

    client._call_model = fake_call  # type: ignore[assignment]

    result = client.complete("sys", "usr")

    assert result == "ok"
    # With attempts clamped to >=1, the primary is still called once.
    assert calls == [PRIMARY_MODEL]


def test_load_config_reads_retry_settings_from_yaml(tmp_path):
    """Production config.yaml drives retry — verifies the wiring."""
    from modules.config import load_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "default_input_path: \"\"\n"
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n"
        "llm_retry_attempts: 4\n"
        "llm_retry_backoff_seconds: 1.5\n",
        encoding="utf-8",
    )

    loaded = load_config(cfg)
    assert loaded.llm_retry_attempts == 4
    assert loaded.llm_retry_backoff_seconds == 1.5


def test_load_config_defaults_retry_settings(tmp_path):
    """Missing retry keys must default to attempts=2 (production retry on)."""
    from modules.config import load_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "default_input_path: \"\"\n"
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n",
        encoding="utf-8",
    )

    loaded = load_config(cfg)
    # Production default: retry once before falling back.
    assert loaded.llm_retry_attempts == 2
    assert loaded.llm_retry_backoff_seconds == 0.5


def test_load_config_clamps_negative_retry_values(tmp_path):
    from modules.config import load_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "default_input_path: \"\"\n"
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n"
        "llm_retry_attempts: -3\n"
        "llm_retry_backoff_seconds: -2.0\n",
        encoding="utf-8",
    )

    loaded = load_config(cfg)
    assert loaded.llm_retry_attempts == 1
    assert loaded.llm_retry_backoff_seconds == 0.0
