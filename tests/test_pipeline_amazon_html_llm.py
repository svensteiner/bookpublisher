"""Tests for the optional Amazon-Description LLM-Pass wiring in pipeline.

The LLM-Pass is gated by ``AppConfig.amazon_html_llm_bullets_enabled``
AND the presence of an ``ANTHROPIC_API_KEY``. These tests cover all four
paths (toggle off, toggle on without key, toggle on with key + happy
path, toggle on with key + LLM crash) using a stub LLM so no network or
real API key is touched.
"""

from __future__ import annotations

from pathlib import Path

from modules.config import AppConfig
from modules.discovery import BookProject
from modules.pipeline import PublisherPipeline
from modules.run_logger import RunLogger
from tests.helpers import runtime_dir


def _project() -> BookProject:
    return BookProject(
        project_id="solidity",
        root=Path("."),
        title="Soliditaet: Wie ich Geschaefte fuehre",
        subtitle="Eine ehrliche Anleitung fuer Operatoren und CFOs",
        amazon_description="Praktisches Sachbuch fuer Operatoren mit Beispielen.",
    )


def _make_config(*, llm_enabled: bool, workspace: Path) -> AppConfig:
    return AppConfig(
        project_root=workspace,
        default_input_path=workspace,
        default_model="fake",
        fallback_model="fake",
        amazon_html_llm_bullets_enabled=llm_enabled,
    )


class _StubLLM:
    """Pipeline.llm replacement — no network, deterministic outputs."""

    def __init__(self, *, api_key: str, response: dict | Exception | None):
        self.api_key = api_key
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, dict)
        return self._response


def _build_pipeline(*, llm_enabled: bool, llm: _StubLLM) -> PublisherPipeline:
    workspace = runtime_dir("amazon_llm_bullets")
    config = _make_config(llm_enabled=llm_enabled, workspace=workspace)
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))
    pipeline.llm = llm  # type: ignore[assignment]
    return pipeline


def test_maybe_extract_returns_none_when_disabled():
    llm = _StubLLM(api_key="sk-ant-fake", response={"bullets": ["x"]})
    pipeline = _build_pipeline(llm_enabled=False, llm=llm)

    result = pipeline._maybe_extract_amazon_llm_bullets(
        _project(), chapter_titles=["Kap 1"]
    )

    assert result is None
    assert llm.calls == []


def test_maybe_extract_returns_none_when_api_key_missing():
    llm = _StubLLM(api_key="", response={"bullets": ["should never be called"]})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)

    result = pipeline._maybe_extract_amazon_llm_bullets(
        _project(), chapter_titles=["Kap 1"]
    )

    assert result is None
    assert llm.calls == []


def test_maybe_extract_returns_bullets_on_happy_path():
    bullets = [
        "Drei Methoden mit Zahlen aus echten Projekten — sofort einsetzbar",
        "Entscheidungsregeln fuer knappe Liquiditaet statt Berater-Floskeln",
        "Checklisten fuer CFO-Monatsabschluss in unter 30 Minuten",
        "Praxisbeispiele aus dem Mittelstand mit dokumentierten Ergebnissen",
        "Ehrliche Risiken statt Erfolgs-Storytelling fuer Operatoren",
    ]
    llm = _StubLLM(api_key="sk-ant-fake", response={"bullets": bullets})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)

    result = pipeline._maybe_extract_amazon_llm_bullets(
        _project(), chapter_titles=["Kap 1", "Kap 2"]
    )

    assert result == bullets
    assert len(llm.calls) == 1


def test_maybe_extract_returns_none_when_llm_raises():
    # extract_amazon_bullets_via_llm catches inner exceptions and returns
    # [], so the pipeline helper sees an empty list and translates to None.
    llm = _StubLLM(api_key="sk-ant-fake", response=RuntimeError("timeout"))
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)

    result = pipeline._maybe_extract_amazon_llm_bullets(
        _project(), chapter_titles=["Kap 1"]
    )

    assert result is None
    assert len(llm.calls) == 1


def test_maybe_extract_returns_none_when_llm_returns_empty():
    llm = _StubLLM(api_key="sk-ant-fake", response={"bullets": []})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)

    result = pipeline._maybe_extract_amazon_llm_bullets(
        _project(), chapter_titles=["Kap 1"]
    )

    assert result is None
