"""Tests for the optional KDP-keyword LLM-Pass wiring in pipeline.

The pass is gated by ``AppConfig.kdp_keywords_llm_enabled`` AND the presence
of an ``ANTHROPIC_API_KEY``. These tests cover all paths (toggle off, toggle
on without key, happy path, LLM crash, empty response) using a stub LLM so no
network or real API key is touched.
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
        kdp_keywords_llm_enabled=llm_enabled,
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
    workspace = runtime_dir("kdp_keywords_llm")
    config = _make_config(llm_enabled=llm_enabled, workspace=workspace)
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))
    pipeline.llm = llm  # type: ignore[assignment]
    return pipeline


def test_returns_none_when_disabled():
    llm = _StubLLM(api_key="sk-ant-fake", response={"keywords": ["x y"]})
    pipeline = _build_pipeline(llm_enabled=False, llm=llm)

    result = pipeline._maybe_extract_kdp_llm_keywords(
        _project(), chapter_titles=["Kap 1"]
    )

    assert result is None
    assert llm.calls == []


def test_returns_none_when_api_key_missing():
    llm = _StubLLM(api_key="", response={"keywords": ["never called"]})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)

    result = pipeline._maybe_extract_kdp_llm_keywords(
        _project(), chapter_titles=["Kap 1"]
    )

    assert result is None
    assert llm.calls == []


def test_returns_phrases_on_happy_path():
    phrases = [
        "liquiditaet steuern mittelstand",
        "cfo monatsabschluss schnell",
        "checklisten fuer operatoren",
    ]
    llm = _StubLLM(api_key="sk-ant-fake", response={"keywords": phrases})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)

    result = pipeline._maybe_extract_kdp_llm_keywords(
        _project(), chapter_titles=["Kap 1", "Kap 2"]
    )

    assert result == phrases
    assert len(llm.calls) == 1


def test_returns_none_when_llm_raises():
    # extract_kdp_keywords_via_llm catches inner exceptions and returns [],
    # so the pipeline helper sees an empty list and translates to None.
    llm = _StubLLM(api_key="sk-ant-fake", response=RuntimeError("timeout"))
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)

    result = pipeline._maybe_extract_kdp_llm_keywords(
        _project(), chapter_titles=["Kap 1"]
    )

    assert result is None
    assert len(llm.calls) == 1


def test_returns_none_when_llm_returns_empty():
    llm = _StubLLM(api_key="sk-ant-fake", response={"keywords": []})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)

    result = pipeline._maybe_extract_kdp_llm_keywords(
        _project(), chapter_titles=["Kap 1"]
    )

    assert result is None


def test_chapter_intros_are_forwarded_to_prompt():
    # When the manuscript yields chapter intros, they must reach the LLM
    # user prompt so the long-tail phrases are grounded in real prose.
    llm = _StubLLM(api_key="sk-ant-fake", response={"keywords": ["liquiditaet planen"]})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    pipeline._collect_chapter_intros = lambda project: [  # type: ignore[assignment]
        ("Kap 1", "Cashflow steuern statt Bauchgefuehl entscheiden.")
    ]

    result = pipeline._maybe_extract_kdp_llm_keywords(
        _project(), chapter_titles=["Kap 1"]
    )

    assert result == ["liquiditaet planen"]
    assert len(llm.calls) == 1
    _, user_prompt = llm.calls[0]
    assert "Kapitel-Eroeffnungen" in user_prompt
    assert "Cashflow steuern statt Bauchgefuehl entscheiden." in user_prompt
