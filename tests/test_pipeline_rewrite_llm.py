"""Tests for the optional rewrite-variant LLM pass wiring in the pipeline.

The pass is gated by ``AppConfig.rewrite_llm_variants_enabled`` AND the
presence of an ``ANTHROPIC_API_KEY``. It works purely on the project's
metadata (title / subtitle / description) — no manuscript read needed.
These tests cover the gate paths (toggle off, no key, no weak fields) plus
the happy path and failure fallback with a stub LLM, so no network is
touched. The config-toggle round-trip is covered too.
"""

from __future__ import annotations

from pathlib import Path

from modules.config import AppConfig, load_config
from modules.discovery import BookProject
from modules.pipeline import PublisherPipeline
from modules.rewrites import (
    REWRITE_SOURCE_LLM,
    RewriteBundle,
    RewriteOption,
    RewriteReport,
    build_rewrite_report,
)
from modules.run_logger import RunLogger
from tests.helpers import runtime_dir


def _project() -> BookProject:
    return BookProject(
        project_id="solidity",
        root=Path("."),
        title="Solid",  # short → diagnosis fires
        subtitle="Eine kompakte Einfuehrung",
        amazon_description="Ein knappes Sachbuch ohne Zahlen.",
    )


def _clean_report() -> RewriteReport:
    bundle = RewriteBundle(
        field="title",
        original="Ein sauberer Titel ohne Probleme",
        diagnosis=[],
        options=[RewriteOption(text="x", char_count=1, keyword_score=0, motivation="m")],
    )
    return RewriteReport(anchors=["titel"], bundles=[bundle])


def _make_config(*, llm_enabled: bool, workspace: Path) -> AppConfig:
    return AppConfig(
        project_root=workspace,
        default_input_path=workspace,
        default_model="fake",
        fallback_model="fake",
        rewrite_llm_variants_enabled=llm_enabled,
    )


class _StubLLM:
    def __init__(self, *, api_key: str, response: dict | Exception):
        self.api_key = api_key
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _build_pipeline(*, llm_enabled: bool, llm: _StubLLM) -> PublisherPipeline:
    workspace = runtime_dir("rewrite_llm_variants")
    config = _make_config(llm_enabled=llm_enabled, workspace=workspace)
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))
    pipeline.llm = llm  # type: ignore[assignment]
    return pipeline


# --- config toggle --------------------------------------------------------


def test_config_default_is_false():
    config = AppConfig(
        project_root=Path("."),
        default_input_path=Path("."),
        default_model="m",
        fallback_model="m",
    )
    assert config.rewrite_llm_variants_enabled is False


def test_load_config_reads_rewrite_llm_toggle(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        'default_input_path: ""\n'
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n"
        "rewrite_llm_variants_enabled: true\n",
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    assert loaded.rewrite_llm_variants_enabled is True


def test_load_config_defaults_rewrite_llm_toggle_false(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        'default_input_path: ""\n'
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n",
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    assert loaded.rewrite_llm_variants_enabled is False


# --- pipeline gate paths --------------------------------------------------


def test_returns_report_unchanged_when_disabled():
    llm = _StubLLM(api_key="sk-ant-fake", response={"variants": []})
    pipeline = _build_pipeline(llm_enabled=False, llm=llm)
    report = build_rewrite_report(_project())

    result = pipeline._maybe_apply_rewrite_variants(_project(), report)

    assert result is report
    assert llm.calls == []


def test_returns_report_unchanged_when_api_key_missing():
    llm = _StubLLM(api_key="", response={"variants": []})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = build_rewrite_report(_project())

    result = pipeline._maybe_apply_rewrite_variants(_project(), report)

    assert result is report
    assert llm.calls == []


def test_returns_report_unchanged_when_no_weak_fields():
    llm = _StubLLM(api_key="sk-ant-fake", response={"variants": []})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = _clean_report()

    result = pipeline._maybe_apply_rewrite_variants(_project(), report)

    assert result is report
    assert llm.calls == []


def test_happy_path_appends_llm_variant():
    llm = _StubLLM(
        api_key="sk-ant-fake",
        response={
            "variants": [
                {"field": "title", "text": "Solide fuehren ohne Hype, mit Methode", "motivation": "Klarer Nutzen."}
            ]
        },
    )
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = build_rewrite_report(_project())
    title_before = len(next(b for b in report.bundles if b.field == "title").options)

    result = pipeline._maybe_apply_rewrite_variants(_project(), report)

    assert result is not report
    title_bundle = next(b for b in result.bundles if b.field == "title")
    assert len(title_bundle.options) == title_before + 1
    assert title_bundle.options[-1].source == REWRITE_SOURCE_LLM
    assert len(llm.calls) == 1


def test_llm_failure_falls_back_to_template_report():
    llm = _StubLLM(api_key="sk-ant-fake", response=RuntimeError("boom"))
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = build_rewrite_report(_project())

    result = pipeline._maybe_apply_rewrite_variants(_project(), report)

    # extract_rewrite_variants_via_llm swallows the exception → empty mapping
    # → apply returns the same instance.
    assert result is report
    assert len(llm.calls) == 1


def test_llm_returning_nothing_leaves_report_unchanged():
    llm = _StubLLM(api_key="sk-ant-fake", response={"variants": []})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = build_rewrite_report(_project())

    result = pipeline._maybe_apply_rewrite_variants(_project(), report)

    assert result is report
    assert len(llm.calls) == 1
