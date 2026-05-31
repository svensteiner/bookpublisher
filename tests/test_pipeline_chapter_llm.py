"""Tests for the optional per-chapter LLM-fix pass wiring in the pipeline.

The pass is gated by ``AppConfig.chapter_review_llm_fixes_enabled`` AND
the presence of an ``ANTHROPIC_API_KEY``. These tests cover the gate
paths (toggle off, no key, no weak chapters) plus the happy path with a
monkeypatched ``extract_docx_chapters`` so no DOCX or network is touched.
"""

from __future__ import annotations

from pathlib import Path

import modules.pipeline as pipeline_mod
from modules.chapters import Chapter, ChapterReport, ChapterScore
from modules.config import AppConfig
from modules.discovery import BookProject
from modules.pipeline import PublisherPipeline
from modules.run_logger import RunLogger
from tests.helpers import runtime_dir


def _project(*, with_manuscript: bool = True) -> BookProject:
    return BookProject(
        project_id="solidity",
        root=Path("."),
        title="Soliditaet",
        manuscript=Path("manuscript.docx") if with_manuscript else None,
    )


def _make_config(*, llm_enabled: bool, workspace: Path) -> AppConfig:
    return AppConfig(
        project_root=workspace,
        default_input_path=workspace,
        default_model="fake",
        fallback_model="fake",
        chapter_review_llm_fixes_enabled=llm_enabled,
    )


class _StubLLM:
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
    workspace = runtime_dir("chapter_llm_fixes")
    config = _make_config(llm_enabled=llm_enabled, workspace=workspace)
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))
    pipeline.llm = llm  # type: ignore[assignment]
    return pipeline


def _score(index: int, *, overall: int, status: str) -> ChapterScore:
    return ChapterScore(
        index=index,
        title=f"Kapitel {index}",
        word_count=500,
        promise=5,
        proof=2,
        value=5,
        transition=5,
        overall=overall,
        status=status,
        fix=f"Heuristischer Fix {index}.",
    )


def _report(*scores: ChapterScore) -> ChapterReport:
    chapters = list(scores)
    avg = round(sum(s.overall for s in chapters) / len(chapters)) if chapters else 0
    return ChapterReport(chapters=chapters, average_score=avg, weakest_chapter_index=1)


def test_returns_report_unchanged_when_disabled():
    llm = _StubLLM(api_key="sk-ant-fake", response={"fixes": [{"index": 1, "fix": "x" * 30}]})
    pipeline = _build_pipeline(llm_enabled=False, llm=llm)
    report = _report(_score(1, overall=50, status="FIX"))

    result = pipeline._maybe_apply_chapter_llm_fixes(_project(), report)

    assert result is report
    assert llm.calls == []


def test_returns_report_unchanged_when_api_key_missing():
    llm = _StubLLM(api_key="", response={"fixes": []})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = _report(_score(1, overall=50, status="FIX"))

    result = pipeline._maybe_apply_chapter_llm_fixes(_project(), report)

    assert result is report
    assert llm.calls == []


def test_returns_report_unchanged_when_no_weak_chapters():
    llm = _StubLLM(api_key="sk-ant-fake", response={"fixes": []})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = _report(_score(1, overall=90, status="READY"))

    result = pipeline._maybe_apply_chapter_llm_fixes(_project(), report)

    assert result is report
    assert llm.calls == []


def test_returns_report_unchanged_when_no_manuscript():
    llm = _StubLLM(api_key="sk-ant-fake", response={"fixes": [{"index": 1, "fix": "x" * 30}]})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = _report(_score(1, overall=50, status="FIX"))

    result = pipeline._maybe_apply_chapter_llm_fixes(
        _project(with_manuscript=False), report
    )

    assert result is report
    assert llm.calls == []


def test_happy_path_enriches_weak_chapter(monkeypatch):
    fix_text = "Verankere Kapitel 1 mit einer konkreten Zahl in Absatz 2."
    llm = _StubLLM(api_key="sk-ant-fake", response={"fixes": [{"index": 1, "fix": fix_text}]})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = _report(
        _score(1, overall=50, status="FIX"),
        _score(2, overall=90, status="READY"),
    )

    monkeypatch.setattr(
        pipeline_mod,
        "extract_docx_chapters",
        lambda path: [
            Chapter(index=1, title="Kapitel 1", body="Schwacher Text.", word_count=500),
            Chapter(index=2, title="Kapitel 2", body="Starker Text.", word_count=500),
        ],
    )

    result = pipeline._maybe_apply_chapter_llm_fixes(_project(), report)

    assert result is not report
    assert result.chapters[0].llm_fix == fix_text
    assert result.chapters[1].llm_fix == ""
    assert len(llm.calls) == 1


def test_extraction_failure_falls_back_to_original(monkeypatch):
    llm = _StubLLM(api_key="sk-ant-fake", response={"fixes": [{"index": 1, "fix": "x" * 30}]})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = _report(_score(1, overall=50, status="FIX"))

    def _boom(path):
        raise RuntimeError("docx unreadable")

    monkeypatch.setattr(pipeline_mod, "extract_docx_chapters", _boom)

    result = pipeline._maybe_apply_chapter_llm_fixes(_project(), report)

    assert result is report
    assert llm.calls == []  # never reached the LLM


def test_llm_returning_nothing_leaves_report_unchanged(monkeypatch):
    llm = _StubLLM(api_key="sk-ant-fake", response={"fixes": []})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = _report(_score(1, overall=50, status="FIX"))

    monkeypatch.setattr(
        pipeline_mod,
        "extract_docx_chapters",
        lambda path: [Chapter(index=1, title="Kapitel 1", body="Text.", word_count=500)],
    )

    result = pipeline._maybe_apply_chapter_llm_fixes(_project(), report)

    # apply_chapter_fixes returns the same instance for an empty mapping.
    assert result is report
    assert len(llm.calls) == 1
